#!/usr/bin/env bash
# Run on the Pi: installs and starts the systemd service so the calendar
# starts on boot and restarts if it crashes.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"

sed -e "s|__USER__|$USER_NAME|g" -e "s|__DIR__|$DIR|g" "$DIR/deploy/wallcal.service" \
  | sudo tee /etc/systemd/system/wallcal.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now wallcal.service
sudo systemctl restart wallcal.service

echo "Installed. Useful commands:"
echo "  systemctl status wallcal      # is it running?"
echo "  journalctl -u wallcal -f      # follow the logs"
echo "  sudo systemctl restart wallcal"
