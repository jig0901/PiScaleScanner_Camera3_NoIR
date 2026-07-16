# Pi Scale + Camera 3 Barcode Scanner

This version deliberately splits the work between two computers:

`Load cell → HX711 → ESP32-S3 → USB serial → Raspberry Pi 4`

`Raspberry Pi Camera Module 3 NoIR → Raspberry Pi barcode scanner`

The ESP32 performs the timing-sensitive HX711 reads. The Pi handles the web
page, calibration controls, autofocus camera preview, and barcode decoding.
Calibration is stored in ESP32 flash and survives restarts.

## 1. Wire the scale to the ESP32-S3

Power everything off first.

| HX711 | ESP32-S3 |
|---|---|
| VCC | 3.3 V |
| GND | GND |
| DOUT / DT | GPIO14 |
| SCK / CLK | GPIO21 |

Connect the load cell to the HX711 labels `E+`, `E-`, `A+`, and `A-`. Wire
colors vary, so use the load-cell datasheet rather than relying on color.

## 2. Flash the ESP32 bridge

1. Install Arduino IDE and the Espressif ESP32 board package.
2. Open `esp32_scale_bridge/esp32_scale_bridge.ino`.
3. Select **ESP32S3 Dev Module** and the correct USB port.
4. Set **USB CDC On Boot: Enabled**.
5. Upload, then open Serial Monitor at 115200 baud. JSON readings should appear.
6. Close Serial Monitor and plug that ESP32 USB cable into the Raspberry Pi.

If your board exposes separate USB and UART connectors, use the connector that
produced the Arduino Serial Monitor output.

## 3. Install on Raspberry Pi 4

Connect Camera Module 3 NoIR to the CSI connector while power is off. Then:

```bash
git clone https://github.com/jig0901/PiScaleScanner_Camera3_NoIR.git
cd PiScaleScanner_Camera3_NoIR
chmod +x install.sh
./install.sh
sudo reboot
```

After reboot, open `http://PI_ADDRESS:8080`. Find the address with `hostname -I`.
The installer adds your user and service to `video` and `dialout`, installs the
Trixie-compatible ZBar packages, and enables automatic startup.

## 4. Calibrate

1. Leave the platform empty and select **Start calibration**.
2. Put a known weight in the center.
3. Enter its weight in grams and select **Finish calibration**.
4. Select **Start scale** if needed. Use **Tare** whenever the empty platform
   does not read zero.

A negative reference unit is valid; it means the load-cell signal direction is
reversed. The web page shows the live raw value, offset, reference unit, USB
device, and any ESP32 error.

The barcode scanner automatically rearms 1.5 seconds after the code leaves the
image. **Scan again** clears it immediately. Camera Module 3 uses continuous
full-range autofocus; **Refocus camera** starts a fresh focus cycle.

## Diagnostics

Check that Linux sees the ESP32:

```bash
ls -l /dev/serial/by-id/
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
groups
```

Check the application:

```bash
systemctl status pi-scale-scanner --no-pager
journalctl -u pi-scale-scanner -f
```

If the ESP32 appears but the page cannot open it, ensure `dialout` appears in
`groups`, reboot, and make sure Arduino Serial Monitor is closed. Only one
program can own the serial port. Test the camera independently with
`rpicam-hello --list-cameras`; the Module 3 NoIR should appear as `imx708_noir`.

If the raw value is fixed at zero or the ESP32 reports DOUT high, the problem is
between the ESP32, HX711, and load cell—not the Pi camera. Recheck 3.3 V, common
ground, GPIO14/GPIO21, and the HX711/load-cell terminals.
