#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RULE="/etc/udev/rules.d/70-8bitdo-ultimate-2c.rules"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required (Python 3.12 or later)." >&2
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
  echo "Python 3.12 or later is required." >&2
  exit 1
fi

sudo install -Dm644 "$ROOT/udev/70-8bitdo-ultimate-2c.rules" "$RULE"
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw

python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT"

cat <<MSG

Installation complete.
Unplug and reconnect the controller and 2.4 GHz receiver.
Then run:
  $ROOT/.venv/bin/python -m u2c_customfw devices
  $ROOT/.venv/bin/python -m u2c_customfw.web      (opens the app in your browser)

Do not run the toolkit with sudo. Use the local desktop session so udev's
TAG+=uaccess permission is applied to your user.
MSG
