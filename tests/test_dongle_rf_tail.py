"""Guarded offline tests of the optional receiver copy-count change."""
import hashlib
from unittest.mock import patch
from tests.test_dongle import STOCK
from u2c_customfw.ops import dongle


def test_tail_patch_exact_stock_instruction():
    edits = dongle.TWEAKS['dongle_rf_tail'][1]
    assert edits == ((0x6c64, bytes.fromhex('14 a2'), bytes.fromhex('1c a2')),)
    assert 0x6c64 == 0x6c60 + 4
    assert 0x843108 + 28 <= 0x843108 + 32


def test_round_trip_with_all_previous_dongle_tweaks():
    raw = STOCK
    sha = hashlib.sha256(raw).hexdigest()
    assert dongle.state(raw, stock_sha256=sha) == (True, ())
    with patch.object(dongle, "STOCK_SHA256", sha):
      for keys in (
        ('dongle_rf_tail',),
        ('dongle_dinput', 'dongle_rf_read', 'dongle_rf_tail'),
        ('dongle_360', 'dongle_dinput', 'dongle_rf_tail', 'dongle_rf_read'),
    ):
          bank = dongle.target(raw, keys)
          assert dongle.state(bank, stock_sha256=sha) == (True, tuple(k for k in dongle.TWEAKS if k in keys))
          assert dongle.target(bank, ()) == raw
          assert bank[0x6c64-dongle.BASE:0x6c66-dongle.BASE] == bytes.fromhex('1c a2')
          assert bank[0x6c60-dongle.BASE:0x6c64-dongle.BASE] == raw[0x6c60-dongle.BASE:0x6c64-dongle.BASE]
          assert bank[0x6c66-dongle.BASE:0x6c80-dongle.BASE] == raw[0x6c66-dongle.BASE:0x6c80-dongle.BASE]
