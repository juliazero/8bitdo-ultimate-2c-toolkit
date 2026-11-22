# SPDX-License-Identifier: GPL-3.0-or-later
"""Private read-only Home ring BLE service (not a HID report or OTA channel)."""
from uuid import UUID

SERVICE_UUID = UUID("d1884d70-3680-4c70-bf6f-4cd08d18c201")
LEVEL_UUID = UUID("d1884d70-3680-4c70-bf6f-4cd08d18c202")


def decode_level(value):
    """Live ring_state.init, ring_state.level. Reject cold or malformed replies."""
    value = bytes(value)
    if len(value) != 2 or value[0] != 1 or value[1] > 5:
        raise ValueError("The controller has not returned an initialized Home ring level.")
    return value[1]
