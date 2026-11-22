#!/usr/bin/env bash
# Build using an installed Python 3.12+ environment and appimagetool.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# Prefer the project's virtual environment for local builds, but GitHub Actions
# installs the build dependencies into setup-python's interpreter on PATH.
if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
  elif command -v python >/dev/null 2>&1; then
    PYTHON="$(command -v python)"
  else
    PYTHON="$(command -v python3)"
  fi
fi
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /path/to/appimagetool" >&2
  exit 2
fi
cd "$ROOT"
exec "$PYTHON" tools/build_desktop.py --appimagetool "$1"
