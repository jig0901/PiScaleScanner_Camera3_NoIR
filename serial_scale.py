import glob
import json
import queue
import threading
import time

import serial


class SerialScale(threading.Thread):
    """Reconnectable JSON-lines client for the ESP32 HX711 bridge."""

    def __init__(self):
        super().__init__(daemon=True)
        self.commands = queue.Queue()
        self.lock = threading.Lock()
        self.connected = False
        self.port = None
        self.raw = self.weight = self.offset = self.reference_unit = None
        self.mode = "disconnected"
        self.calibration_active = False
        self.error = "Connect the ESP32-S3 USB cable"
        self.last_reading = 0.0
        self.next_id = 1
        self._pending = {}

    @staticmethod
    def ports():
        preferred = sorted(glob.glob("/dev/serial/by-id/*"))
        fallback = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
        return preferred + [p for p in fallback if p not in preferred]

    def run(self):
        while True:
            found = False
            for port in self.ports():
                try:
                    with serial.Serial(port, 115200, timeout=0.15, write_timeout=1) as link:
                        found = True
                        time.sleep(2.0)  # Some ESP32 USB serial ports reset on open.
                        link.reset_input_buffer()
                        with self.lock:
                            self.connected, self.port, self.error = True, port, None
                        self._session(link)
                except (OSError, serial.SerialException) as exc:
                    with self.lock:
                        self.connected = False
                        self.error = f"ESP32 serial disconnected: {exc}"
            if not found:
                with self.lock:
                    self.connected, self.port = False, None
                    self.error = "ESP32 not found; connect USB and check the dialout group"
            time.sleep(1.0)

    def _session(self, link):
        while True:
            try:
                while True:
                    command = self.commands.get_nowait()
                    if time.monotonic() <= command["deadline"]:
                        link.write((command["line"] + "\n").encode())
                        link.flush()
            except queue.Empty:
                pass
            line = link.readline()
            if line:
                try:
                    message = json.loads(line.decode(errors="replace"))
                    self._handle(message)
                except (ValueError, UnicodeError):
                    pass

    def _handle(self, message):
        if message.get("type") == "reading":
            with self.lock:
                self.raw = message.get("raw")
                self.weight = message.get("weight_g")
                self.offset = message.get("offset")
                self.reference_unit = message.get("reference_unit")
                self.mode = "calibration" if self.calibration_active else message.get("mode", "scale")
                self.last_reading = time.monotonic()
                self.error = message.get("error")
        message_id = message.get("id")
        if message_id is not None:
            for pending in list(getattr(self, "_pending", {}).values()):
                if pending["id"] == message_id:
                    pending["response"] = message
                    pending["event"].set()

    def _command(self, command, argument=None, timeout=8):
        with self.lock:
            if not self.connected:
                raise RuntimeError(self.error or "ESP32 is not connected")
            command_id = self.next_id
            self.next_id += 1
        pending = {"id": command_id, "event": threading.Event(), "response": None}
        self._pending[command_id] = pending
        line = command + (f" {argument}" if argument is not None else "") + f" {command_id}"
        self.commands.put({"line": line, "deadline": time.monotonic() + timeout})
        if not pending["event"].wait(timeout):
            self._pending.pop(command_id, None)
            raise TimeoutError(f"ESP32 did not finish {command.lower()}")
        response = self._pending.pop(command_id)["response"]
        if response.get("type") == "error":
            raise RuntimeError(response.get("error", "ESP32 command failed"))
        return response

    def tare(self):
        return self._command("TARE")

    def begin_calibration(self):
        response = self._command("TARE")
        with self.lock:
            self.calibration_active = True
            self.mode = "calibration"
        return response

    def calibrate(self, grams):
        response = self._command("CALIBRATE", f"{grams:.3f}", timeout=12)
        with self.lock:
            self.calibration_active = False
            self.mode = "scale"
        return response

    def start_scale(self):
        response = self._command("START")
        with self.lock:
            self.calibration_active = False
            self.mode = "scale"
        return response

    def status(self):
        with self.lock:
            fresh = self.connected and time.monotonic() - self.last_reading < 2.0
            return {"raw": self.raw, "weight_g": self.weight,
                    "offset": self.offset, "reference_unit": self.reference_unit,
                    "scale_mode": self.mode, "scale_ready": fresh and self.error is None,
                    "scale_port": self.port, "error": self.error}
