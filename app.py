#!/usr/bin/env python3
import json
import statistics
import threading
import time
from collections import deque
from pathlib import Path

import cv2
from flask import Flask, Response, jsonify, render_template, request
from gpiozero import DigitalInputDevice, DigitalOutputDevice
from libcamera import controls
from picamera2 import Picamera2
from pyzbar.pyzbar import decode as decode_barcodes
from vendor.hx711 import HX711 as RepoHX711


BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "scale_config.json"
DEFAULT_CONFIG = {"dout_pin": 5, "sck_pin": 6, "offset": 0.0, "scale": 1.0}


def load_config():
    try:
        return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
    except (OSError, ValueError, TypeError):
        return DEFAULT_CONFIG.copy()


def save_config(config):
    temporary = CONFIG_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2))
    temporary.replace(CONFIG_FILE)


class HX711:
    def __init__(self, dout_pin, sck_pin):
        # HX711 actively drives DOUT. Do not enable GPIO Zero's pull-up mode:
        # pull_up=True also makes LOW the logical active state and inverts
        # .value, causing ready/data bits to be read backwards.
        self.dout = DigitalInputDevice(
            dout_pin, pull_up=None, active_state=True
        )
        self.sck = DigitalOutputDevice(sck_pin, initial_value=False)
        self.lock = threading.Lock()

    def _wait_ready(self, timeout):
        deadline = time.monotonic() + timeout
        while self.dout.value:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.001)
        return True

    def _recover(self):
        # Intentionally power down and wake the HX711, then wait for conversion.
        self.sck.on()
        time.sleep(0.001)
        self.sck.off()
        time.sleep(0.15)

    def read_raw(self, timeout=0.75):
        # Lock the complete transaction, including ready-wait. This prevents
        # HTTP tare/calibration reads from colliding with the worker thread.
        with self.lock:
            self.sck.off()
            if not self._wait_ready(timeout):
                self._recover()
                if not self._wait_ready(timeout):
                    raise TimeoutError("HX711 DOUT stayed high after automatic reset")

            value = 0
            try:
                for _ in range(24):
                    self.sck.on()
                    value = (value << 1) | int(self.dout.value)
                    self.sck.off()
                # One extra pulse selects channel A, gain 128 next time.
                self.sck.on()
                self.sck.off()
            finally:
                self.sck.off()

        # 0xFFFFFF is signed -1 and 0x000000 is 0, so both are legitimate
        # readings near zero. Only the two signed rail values are saturation.
        if value in (0x7FFFFF, 0x800000):
            raise ValueError(f"Rejected saturated HX711 sample 0x{value:06X}")

        if value & 0x800000:
            value -= 1 << 24
        return value

    def close(self):
        self.sck.close()
        self.dout.close()


class ScaleWorker(threading.Thread):
    def __init__(self, config):
        super().__init__(daemon=True)
        self.config = config
        self.hx = RepoHX711(config["dout_pin"], config["sck_pin"])
        self.hx.set_offset(config.get("offset", 0.0))
        self.hx.set_reference_unit(config.get("scale", 1.0))
        self.operation_lock = threading.Lock()
        self.mode = "scale"
        self.samples = deque(maxlen=15)
        self.raw = None
        self.weight = None
        self.error = None
        self.good_samples = 0
        self.data_lock = threading.Lock()

    def run(self):
        while True:
            if self.mode != "scale":
                time.sleep(0.1)
                continue
            try:
                with self.operation_lock:
                    raw = self.hx.read_median(5)
                    self.hx.power_down()
                    self.hx.power_up()
                self.samples.append(raw)
                filtered = statistics.median(self.samples)
                scale = float(self.config.get("scale", 1.0))
                weight = None if scale == 0 else (filtered - self.config["offset"]) / scale
                with self.data_lock:
                    self.raw, self.weight, self.error = raw, weight, None
                    self.good_samples += 1
            except Exception as exc:
                with self.data_lock:
                    self.error = str(exc)
                    self.weight = None
                try:
                    self.hx.reset()
                except Exception:
                    pass
                time.sleep(0.25)

    def average_raw(self, count=20, timeout=5.0):
        with self.operation_lock:
            return self.hx.read_average(count)

    def tare(self):
        with self.operation_lock:
            offset = self.hx.tare(25)
        self.config["offset"] = offset
        self.samples.clear()
        save_config(self.config)
        return offset

    def begin_calibration(self):
        self.mode = "calibration"
        try:
            offset = self.tare()
            with self.data_lock:
                self.error = None
            return offset
        except Exception:
            self.mode = "paused"
            raise

    def calibrate(self, known_grams):
        raw = self.average_raw()
        difference = raw - self.config["offset"]
        if abs(difference) < 100:
            raise ValueError("Raw reading did not change enough; check the load cell")
        self.config["scale"] = difference / known_grams
        self.hx.set_offset(self.config["offset"])
        self.hx.set_reference_unit(self.config["scale"])
        self.samples.clear()
        save_config(self.config)
        self.mode = "scale"
        return self.config["scale"]

    def start_scale(self):
        self.hx.set_offset(self.config.get("offset", 0.0))
        self.hx.set_reference_unit(self.config.get("scale", 1.0))
        self.samples.clear()
        self.mode = "scale"

    def status(self):
        with self.data_lock:
            return {"raw": self.raw, "weight_g": self.weight,
                    "scale_ready": self.good_samples >= 5 and self.error is None,
                    "scale_mode": self.mode, "reference_unit": self.config.get("scale"),
                    "offset": self.config.get("offset"), "error": self.error}


class CameraWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.picam = Picamera2()
        self.camera_model = self.picam.camera_properties.get("Model", "unknown")
        camera_config = self.picam.create_video_configuration(
            # Extra resolution helps the decoder distinguish narrow UPC/EAN bars.
            main={"size": (1536, 864), "format": "RGB888"},
            controls={"FrameRate": 15},
            buffer_count=4,
        )
        self.picam.configure(camera_config)
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
            # Camera Module 3 (IMX708) supports PDAF continuous autofocus. Full
            # range includes the close working distances commonly used here.
            focus_controls = {
                "AfMode": controls.AfModeEnum.Continuous,
                "AfRange": controls.AfRangeEnum.Full,
                "AfSpeed": controls.AfSpeedEnum.Fast,
                "AeEnable": True,
                "AwbEnable": True,
                "Sharpness": 1.5,
            }
            supported = self.picam.camera_controls
            self.picam.set_controls({key: value for key, value in focus_controls.items()
                                     if key in supported})
            frame_number = 0
            while True:
                image = self.picam.capture_array()
                frame_number += 1
                if frame_number % 3 == 0:
                    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
                    codes = decode_barcodes(gray)
                    if codes:
                        code = codes[0]
                        decoded = code.data.decode("utf-8", errors="replace")
                        with self.data_lock:
                            if decoded != self.barcode:
                                self.scan_count += 1
                            self.barcode = decoded
                            self.barcode_type = code.type
                            self.barcode_seen_at = time.monotonic()
                        x, y, w, h = code.rect
                        cv2.rectangle(image, (x, y), (x + w, y + h), (30, 220, 80), 3)
                    else:
                        # Rearm automatically after the code has left the view.
                        with self.data_lock:
                            if (self.barcode_seen_at is not None and
                                    time.monotonic() - self.barcode_seen_at > 1.5):
                                self.barcode = None
                                self.barcode_type = None
                                self.barcode_seen_at = None
                # OpenCV expects BGR when encoding.
                bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                ok, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 75])
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
            self.barcode = None
            self.barcode_type = None
            self.barcode_seen_at = None

    def refocus(self):
        supported = self.picam.camera_controls
        values = {}
        if "AfMode" in supported:
            values["AfMode"] = controls.AfModeEnum.Auto
        if "AfRange" in supported:
            values["AfRange"] = controls.AfRangeEnum.Full
        if "AfSpeed" in supported:
            values["AfSpeed"] = controls.AfSpeedEnum.Fast
        if "AfTrigger" in supported:
            values["AfTrigger"] = controls.AfTriggerEnum.Start
        if not values:
            raise RuntimeError("This camera does not expose autofocus controls")
        self.picam.set_controls(values)

        # Return to continuous focus after the one-shot focus cycle settles.
        def restore_continuous():
            time.sleep(2.0)
            if "AfMode" in self.picam.camera_controls:
                self.picam.set_controls({"AfMode": controls.AfModeEnum.Continuous})

        threading.Thread(target=restore_continuous, daemon=True).start()


app = Flask(__name__)
scale_config = load_config()
scale = ScaleWorker(scale_config)
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
        return jsonify(ok=True, offset=scale.tare())
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/calibration/start")
def calibration_start():
    try:
        return jsonify(ok=True, offset=scale.begin_calibration())
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/calibrate")
def calibrate():
    try:
        grams = float(request.get_json(force=True)["grams"])
        if grams <= 0:
            raise ValueError("Calibration weight must be greater than zero")
        return jsonify(ok=True, scale=scale.calibrate(grams))
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/scale/start")
def scale_start():
    try:
        scale.start_scale()
        return jsonify(ok=True)
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
