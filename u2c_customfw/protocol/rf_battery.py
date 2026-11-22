# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental 2.4GHz battery trailer v1, occupying RF packet bytes 20..27.

The factory transmitter copies only 20 bytes; this format is solely used by
an optional extended-payload tweak. Reject unmodified/stale/unknown packets.
"""


def parse(packet):
    data = bytes(packet)
    if len(data) < 28 or data[0] not in (0x8d, 0x8e, 0x8f, 0x90):
        return None
    trailer = data[20:28]
    if trailer[:2] != b'\xa5\x5a':
        return None
    if trailer[2] > 100 or trailer[5] & ~3:
        return None
    check = 0xc3
    for value in trailer[:7]:
        check ^= value
    if check != trailer[7]:
        return None
    mv = trailer[3] | (trailer[4] << 8)
    if not 2900 <= mv <= 4600:
        return None
    return {'percent': trailer[2], 'mv': mv, 'full': bool(trailer[5] & 1),
            'charging': bool(trailer[5] & 2), 'sequence': trailer[6]}
