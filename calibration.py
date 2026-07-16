#!/usr/bin/env python3
"""Standalone terminal calibration based on the Hugh Evans workflow."""

import json
from pathlib import Path

from vendor.hx711 import HX711

CONFIG = Path(__file__).with_name("scale_config.json")


def main():
    hx = HX711(5, 6)
    try:
        print("Remove all weight from the scale. Taring 25 samples...")
        offset = hx.tare(25)
        grams = float(input("Place the known weight and enter its grams: "))
        if grams <= 0:
            raise ValueError("Weight must be greater than zero")
        raw = hx.read_average(25)
        reference = (raw - offset) / grams
        config = {"dout_pin": 5, "sck_pin": 6,
                  "offset": offset, "scale": reference}
        CONFIG.write_text(json.dumps(config, indent=2))
        print(f"Offset: {offset:.2f}")
        print(f"Reference unit: {reference:.6f}")
        print(f"Saved to {CONFIG}")
    finally:
        hx.cleanup()


if __name__ == "__main__":
    main()

