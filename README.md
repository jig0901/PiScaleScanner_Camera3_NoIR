# Raspberry Pi 4 Scale + Barcode Scanner — Camera Module 3 NoIR

For a Raspberry Pi 4, Raspberry Pi Camera Module 3 NoIR (IMX708), HX711, and
load cell. The camera and scale run independently so a slow HX711 read cannot
freeze the preview. Camera Module 3 runs continuous full-range autofocus, with
a **Refocus camera** button for an immediate focus cycle.

## Wiring

Always shut down and unplug the Pi before connecting the camera ribbon.

| HX711 | Raspberry Pi 4 |
|---|---|
| VCC | 3.3 V, physical pin 1 |
| GND | Ground, physical pin 6 |
| DOUT / DT | GPIO5, physical pin 29 |
| SCK / CLK | GPIO6, physical pin 31 |

Do not power HX711 VCC from 5 V. Raspberry Pi GPIO is not 5 V tolerant.

Typical four-wire load-cell colors are not universal. Follow the labels on
your HX711/load-cell documentation for E+, E-, A+, and A-.

## Raspberry Pi setup

1. Install Raspberry Pi OS 64-bit and complete Wi-Fi setup.
2. Connect the Camera Module 3 ribbon to the Pi 4 CSI connector with power off.
3. Copy this entire folder to the Pi.
4. In a terminal, run:

   ```bash
   cd PiScaleScanner
   chmod +x install.sh
   ./install.sh
   sudo reboot
   ```

5. After reboot, find the address with `hostname -I`, then open
   `http://PI_ADDRESS:8080` from a device on the same network.

The installer supports Raspberry Pi OS Trixie, where the ZBar library package
is named `libzbar0t64` and `pyzbar` must be installed into a virtual
environment. Do not run the installer with `sudo`; it invokes `sudo` only for
the individual system operations that require it.

## First calibration

1. Remove everything from the platform and select **Start calibration**. This
   switches out of continuous scale mode and tares 25 samples.
2. When instructed, place an accurately known weight in the center.
3. Enter its weight in grams and select **Finish calibration**.
4. The saved reference unit is applied and **Start scale** mode resumes.
   Calibration is stored in `scale_config.json` and survives restarts.

The page has separate **Start calibration** and **Start scale** buttons so the
two workflows cannot drive the HX711 at the same time. Live raw value, offset,
reference unit, mode, and errors are visible in the interface.

After a barcode leaves the camera view, the scanner automatically rearms in
about 1.5 seconds. Use **Scan again** to clear the result immediately.

A negative calibration factor is valid when the load-cell signal direction is
reversed. It will still display positive weights after calibration.

## Standalone scale tests

Stop the web service before using either script because only one process may
own GPIO5/GPIO6:

```bash
sudo systemctl stop pi-scale-scanner
.venv/bin/python calibration.py
.venv/bin/python scale.py
sudo systemctl start pi-scale-scanner
```

`calibration.py` implements the guide's trimmed 25-sample calibration flow.
`scale.py` provides continuous five-sample median readings and performs the
guide's HX711 power-down/power-up sequence between displayed weights.

## Camera Module 3 NoIR

The IMX708 autofocus is enabled continuously over its full focus range. Hold a
barcode still at the intended working distance and use **Refocus camera** if it
does not lock promptly. Do not twist the Camera Module 3 lens.

NoIR means the sensor has no infrared-cut filter; it does not mean the camera
supplies infrared light. Printed barcodes decode most reliably with bright,
even visible lighting that does not create glare. An IR illuminator is useful
for night vision, but many inks and packages have poor IR contrast.

Test the camera independently with:

```bash
rpicam-hello --timeout 0
```

## Diagnostics

```bash
systemctl status pi-scale-scanner
journalctl -u pi-scale-scanner -f
```

If the camera is missing, run `rpicam-hello --list-cameras`; Camera Module 3
should identify as `imx708`. If DOUT remains high, verify HX711 power, common
ground, and the GPIO5/GPIO6 wiring.

The HX711 implementation is adapted from `tatobari/hx711py`, the driver used by
the Hugh Evans guide. It samples DOUT after returning SCK LOW, uses median and
trimmed-mean sampling, and power-cycles between displayed weights. The vendored
copy adds Trixie-compatible GPIO allocation and a ready timeout so a missing
HX711 cannot freeze the web server. See `THIRD_PARTY_NOTICES.md`.
