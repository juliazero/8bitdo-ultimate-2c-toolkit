#!/usr/bin/env bash
# Compatibility shim; preferred entry point: linux/install.sh
set -euo pipefail
exec "$(dirname -- "${BASH_SOURCE[0]}")/linux/install.sh" "$@"
