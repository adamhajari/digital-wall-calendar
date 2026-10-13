#!/usr/bin/env bash
# Run on your laptop: copies the code to the Pi and restarts the calendar.
#
#   scripts/deploy.sh                 # uses pi@wallcal.local
#   scripts/deploy.sh me@10.0.0.42    # or any ssh destination
#
# config.toml, secrets/, data/ and the virtualenv on the Pi are left alone.
set -euo pipefail

PI="${1:-${WALLCAL_PI:-pi@wallcal.local}}"
DEST="wallcal"  # relative to the Pi user's home directory

cd "$(dirname "$0")/.."
rsync -rlptz --delete --itemize-changes \
  --exclude .venv --exclude data --exclude secrets --exclude config.toml \
  --exclude __pycache__ --exclude .git --exclude preview.png --exclude .req.sha256 \
  ./ "$PI:$DEST/"

ssh "$PI" bash -s <<EOF
set -euo pipefail
cd ~/$DEST
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
# Only reinstall packages when the requirements changed (slow on a Pi Zero).
if ! sha256sum --status -c .req.sha256 2>/dev/null; then
  .venv/bin/pip install -r requirements-pi.txt
  sha256sum requirements.txt requirements-pi.txt > .req.sha256
fi
if [ -f /etc/systemd/system/wallcal.service ]; then
  sudo systemctl restart wallcal
  sleep 3
  systemctl --no-pager --lines=5 status wallcal || true
else
  echo "Service not installed yet: run ~/$DEST/scripts/install-service.sh on the Pi."
fi
EOF
