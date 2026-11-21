# SPDX-License-Identifier: GPL-3.0-or-later
import logging
from pathlib import Path
import tempfile
import unittest

from u2c_customfw.ops.log import get_logger
from u2c_customfw.ops.report import log_tail


class LogEncodingTest(unittest.TestCase):
    def test_unicode_installation_message_round_trips_through_log_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            log = get_logger(store)
            try:
                message = "Installed. Settings → Bluetooth. Zażółć gęślą jaźń."
                log.info(message)
                for handler in log.handlers:
                    handler.flush()
                self.assertIn(message, (store / "u2c-customfw.log").read_text(encoding="utf-8"))
                self.assertIn(message, log_tail(store))
            finally:
                for handler in list(log.handlers):
                    if isinstance(handler, logging.FileHandler):
                        log.removeHandler(handler)
                        handler.close()

    def test_legacy_non_utf8_log_does_not_break_problem_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            (store / "u2c-customfw.log").write_bytes(b"old log: \x9c\nlast line\n")
            self.assertIn("last line", log_tail(store))
