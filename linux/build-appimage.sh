#!/usr/bin/env bash
# Build using an installed Python 3.12+ environment and appimagetool.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /path/to/appimagetool" >&2
  exit 2
fi
cd "$ROOT"
exec "$PYTHON" tools/build_desktop.py --appimagetool "$1"
