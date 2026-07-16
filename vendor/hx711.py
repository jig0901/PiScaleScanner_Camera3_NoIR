"""HX711 driver adapted from tatobari/hx711py (Apache-2.0).

Original: https://github.com/tatobari/hx711py
Modifications: Trixie-compatible output allocation, bounded ready wait,
context-managed locking, and Python 3 cleanup. The important upstream timing
behavior is preserved: DOUT is sampled after SCK returns LOW.
"""

import statistics
import threading
import time

import RPi.GPIO as GPIO


class HX711:
    def __init__(self, dout, pd_sck, gain=128, ready_timeout=1.0):
        self.DOUT = dout
        self.PD_SCK = pd_sck
        self.ready_timeout = ready_timeout
        self.read_lock = threading.Lock()
        self.reference_unit = 1.0
        self.offset = 0.0
        self.last_value = None
        self.gain_pulses = {128: 1, 64: 3, 32: 2}[gain]

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        # initial=LOW fixes allocation with Raspberry Pi OS Trixie's
        # rpi-lgpio RPi.GPIO compatibility implementation.
        GPIO.setup(self.PD_SCK, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.DOUT, GPIO.IN)
        self.reset()

    def _wait_ready(self):
        deadline = time.monotonic() + self.ready_timeout
        while GPIO.input(self.DOUT) != GPIO.LOW:
            if time.monotonic() >= deadline:
                raise TimeoutError("HX711 DOUT stayed high")
            time.sleep(0.001)

    def _read_bit(self):
        # Upstream hx711py samples after lowering SCK, when DOUT is stable.
        GPIO.output(self.PD_SCK, GPIO.HIGH)
        GPIO.output(self.PD_SCK, GPIO.LOW)
        return int(GPIO.input(self.DOUT))

    def read_raw(self):
        with self.read_lock:
            GPIO.output(self.PD_SCK, GPIO.LOW)
            self._wait_ready()
            value = 0
            for _ in range(24):
                value = (value << 1) | self._read_bit()
            for _ in range(self.gain_pulses):
                self._read_bit()

        if value & 0x800000:
            value -= 1 << 24
        self.last_value = value
        return value

    def read_median(self, times=3):
        if times < 1:
            raise ValueError("times must be at least 1")
        return statistics.median(self.read_raw() for _ in range(times))

    def read_average(self, times=15):
        if times < 1:
            raise ValueError("times must be at least 1")
        values = sorted(self.read_raw() for _ in range(times))
        if times >= 5:
            trim = max(1, int(times * 0.2))
            values = values[trim:-trim]
        return statistics.mean(values)

    def set_reference_unit(self, value):
        value = float(value)
        if value == 0:
            raise ValueError("reference unit cannot be zero")
        self.reference_unit = value

    def set_offset(self, value):
        self.offset = float(value)

    def tare(self, times=25):
        self.offset = self.read_average(times)
        return self.offset

    def get_weight(self, times=5):
        return (self.read_median(times) - self.offset) / self.reference_unit

    def power_down(self):
        with self.read_lock:
            GPIO.output(self.PD_SCK, GPIO.LOW)
            GPIO.output(self.PD_SCK, GPIO.HIGH)
            time.sleep(0.0001)

    def power_up(self):
        with self.read_lock:
            GPIO.output(self.PD_SCK, GPIO.LOW)
            time.sleep(0.0001)

    def reset(self):
        self.power_down()
        self.power_up()
        time.sleep(0.15)

    def cleanup(self):
        GPIO.output(self.PD_SCK, GPIO.LOW)
        GPIO.cleanup((self.DOUT, self.PD_SCK))

