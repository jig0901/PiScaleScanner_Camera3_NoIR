#!/bin/bash
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Run this script as your normal Raspberry Pi user, not with sudo."
  exit 1
fi

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="$(id -un)"

sudo apt update
sudo apt install -y python3-flask python3-opencv python3-picamera2 \
  python3-venv python3-pip python3-serial libzbar0t64

# Trixie no longer provides python3-pyzbar. Keep Raspberry Pi's camera and
# OpenCV packages visible while installing only pyzbar from PyPI.
python3 -m venv --system-site-packages "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pyzbar
sudo usermod -aG video,dialout "$USER_NAME"

sed -e "s|@USER@|$USER_NAME|g" -e "s|@APP_DIR@|$APP_DIR|g" \
  "$APP_DIR/pi-scale-scanner.service.in" | sudo tee /etc/systemd/system/pi-scale-scanner.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now pi-scale-scanner.service

echo
echo "Installed. Reboot once so camera/serial group changes take effect:"
echo "  sudo reboot"
echo "After reboot, open: http://$(hostname -I | awk '{print $1}'):8080"
