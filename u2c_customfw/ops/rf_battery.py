# SPDX-License-Identifier: GPL-3.0-or-later
"""Read controller battery telemetry forwarded by a patched 2.4 GHz receiver."""
from ..protocol import app
from ..protocol.rf_battery import parse


RF_BUFFER = 0x843108


def read(ch):
    # No write, no firmware-update session; only dongle_rf_read intercepts RAM.
    raw = app.read(ch, RF_BUFFER, 32, tries=1)
    return parse(raw) if raw is not None else None
