#!/usr/bin/env bash
# Run on the Pi: installs and starts the systemd service so the calendar
# starts on boot and restarts if it crashes.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"

sed -e "s|__USER__|$USER_NAME|g" -e "s|__DIR__|$DIR|g" "$DIR/deploy/wallcal.service" \
  | sudo tee /etc/systemd/system/wallcal.service > /dev/null

# Let the service user power off without a password (the frame's shutdown buttons need it).
# Only this one command; everything else still needs a password. Checked before installing,
# since a broken sudoers file would lock out sudo entirely.
SUDOERS="$(mktemp)"
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff" > "$SUDOERS"
sudo visudo -cqf "$SUDOERS"
sudo install -m 0440 -o root -g root "$SUDOERS" /etc/sudoers.d/wallcal-poweroff
rm -f "$SUDOERS"

sudo systemctl daemon-reload
sudo systemctl enable --now wallcal.service
sudo systemctl restart wallcal.service

echo "Installed. Useful commands:"
echo "  systemctl status wallcal      # is it running?"
echo "  journalctl -u wallcal -f      # follow the logs"
echo "  sudo systemctl restart wallcal"
