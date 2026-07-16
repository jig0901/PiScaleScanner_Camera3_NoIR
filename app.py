#!/usr/bin/env python3
import threading
import time

import cv2
from flask import Flask, Response, jsonify, render_template, request
from libcamera import controls
from picamera2 import Picamera2
from pyzbar.pyzbar import decode as decode_barcodes

from serial_scale import SerialScale


class CameraWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.picam = Picamera2()
        self.camera_model = self.picam.camera_properties.get("Model", "unknown")
        config = self.picam.create_video_configuration(
            main={"size": (1536, 864), "format": "RGB888"},
            controls={"FrameRate": 15}, buffer_count=4)
        self.picam.configure(config)
        self.frame = None
        self.barcode = None
        self.barcode_type = None
        self.barcode_seen_at = None
        self.scan_count = 0
        self.error = None
        self.condition = threading.Condition()
        self.data_lock = threading.Lock()

    def run(self):
        try:
            self.picam.start()
            desired = {"AfMode": controls.AfModeEnum.Continuous,
                       "AfRange": controls.AfRangeEnum.Full,
                       "AfSpeed": controls.AfSpeedEnum.Fast,
                       "AeEnable": True, "AwbEnable": True, "Sharpness": 1.5}
            self.picam.set_controls({k: v for k, v in desired.items()
                                     if k in self.picam.camera_controls})
            frame_number = 0
            while True:
                image = self.picam.capture_array()
                frame_number += 1
                if frame_number % 3 == 0:
                    codes = decode_barcodes(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY))
                    if codes:
                        code = codes[0]
                        decoded = code.data.decode("utf-8", errors="replace")
                        with self.data_lock:
                            if decoded != self.barcode:
                                self.scan_count += 1
                            self.barcode, self.barcode_type = decoded, code.type
                            self.barcode_seen_at = time.monotonic()
                        x, y, w, h = code.rect
                        cv2.rectangle(image, (x, y), (x + w, y + h), (30, 220, 80), 3)
                    else:
                        with self.data_lock:
                            if (self.barcode_seen_at is not None and
                                    time.monotonic() - self.barcode_seen_at > 1.5):
                                self.barcode = self.barcode_type = self.barcode_seen_at = None
                ok, encoded = cv2.imencode(
                    ".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
                    [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    with self.condition:
                        self.frame = encoded.tobytes()
                        self.condition.notify_all()
        except Exception as exc:
            self.error = str(exc)
            with self.condition:
                self.condition.notify_all()

    def next_frame(self):
        with self.condition:
            self.condition.wait(timeout=2.0)
            return self.frame

    def status(self):
        with self.data_lock:
            return {"barcode": self.barcode, "barcode_type": self.barcode_type,
                    "scan_count": self.scan_count, "camera_model": self.camera_model,
                    "camera_error": self.error}

    def rearm(self):
        with self.data_lock:
            self.barcode = self.barcode_type = self.barcode_seen_at = None

    def refocus(self):
        supported = self.picam.camera_controls
        values = {}
        for key, value in (("AfMode", controls.AfModeEnum.Auto),
                           ("AfRange", controls.AfRangeEnum.Full),
                           ("AfSpeed", controls.AfSpeedEnum.Fast),
                           ("AfTrigger", controls.AfTriggerEnum.Start)):
            if key in supported:
                values[key] = value
        if not values:
            raise RuntimeError("This camera does not expose autofocus controls")
        self.picam.set_controls(values)

        def restore():
            time.sleep(2)
            if "AfMode" in self.picam.camera_controls:
                self.picam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        threading.Thread(target=restore, daemon=True).start()


app = Flask(__name__)
scale = SerialScale()
camera = CameraWorker()


@app.get("/")
def index():
    return render_template("index.html")


def frame_generator():
    while True:
        frame = camera.next_frame()
        if frame:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"


@app.get("/video")
def video():
    return Response(frame_generator(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/status")
def status():
    return jsonify({**scale.status(), **camera.status()})


@app.post("/api/tare")
def tare():
    try:
        return jsonify(ok=True, **scale.tare())
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/calibration/start")
def calibration_start():
    try:
        return jsonify(ok=True, **scale.begin_calibration())
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/calibrate")
def calibrate():
    try:
        grams = float(request.get_json(force=True)["grams"])
        if grams <= 0:
            raise ValueError("Calibration weight must be greater than zero")
        return jsonify(ok=True, **scale.calibrate(grams))
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/scale/start")
def scale_start():
    try:
        return jsonify(ok=True, **scale.start_scale())
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/rearm")
def rearm():
    camera.rearm()
    return jsonify(ok=True)


@app.post("/api/refocus")
def refocus():
    try:
        camera.refocus()
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


if __name__ == "__main__":
    scale.start()
    camera.start()
    app.run(host="0.0.0.0", port=8080, threaded=True)
