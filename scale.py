#!/usr/bin/env python3
"""Standalone continuous scale test."""

import json
import time
from pathlib import Path

from vendor.hx711 import HX711

CONFIG = Path(__file__).with_name("scale_config.json")


def main():
    config = json.loads(CONFIG.read_text())
    hx = HX711(config.get("dout_pin", 5), config.get("sck_pin", 6))
    hx.set_offset(config["offset"])
    hx.set_reference_unit(config["scale"])
    print("Scale ready. Press Ctrl+C to stop.")
    try:
        while True:
            print(f"Weight: {hx.get_weight(5):.1f} g")
            hx.power_down()
            hx.power_up()
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        hx.cleanup()


if __name__ == "__main__":
    main()

