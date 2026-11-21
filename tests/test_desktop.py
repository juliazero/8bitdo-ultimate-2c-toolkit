# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import sys
import threading
import unittest
from pathlib import Path
from u2c_customfw.desktop import Session, smoke_test, WindowLifetime
from unittest.mock import Mock


class DesktopTest(unittest.TestCase):
    def test_shutdown_waits_for_device_and_rejects_new_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            runner = session.server.runner
            self.assertTrue(runner.claim())
            stopped = threading.Event()
            worker = threading.Thread(target=lambda: (session.close(), stopped.set()))
            worker.start()
            with runner.cond:
                self.assertTrue(runner.cond.wait_for(lambda: runner.closing, timeout=2))
            self.assertFalse(stopped.is_set())
            self.assertFalse(runner.claim())
            runner.release()
            worker.join(5)
            self.assertTrue(stopped.is_set())
            self.assertFalse(runner.claim())
            self.assertFalse(runner.start("backup"))

    def test_window_stays_open_until_operation_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            self.assertTrue(session.server.runner.claim())
            window = Mock()
            lifetime = WindowLifetime(session, window)
            self.assertFalse(lifetime.closing())
            self.assertFalse(lifetime.closing())
            window.destroy.assert_not_called()
            session.server.runner.release()
            self.assertTrue(lifetime.finished.wait(5))
            # The worker sets finished just before dispatching window destruction.
            self.assertTrue(lifetime.closing())
            self.assertFalse(session.server.runner.claim())

    @unittest.skipIf(sys.platform == "darwin", "macOS uses the Swift launcher")
    def test_smoke_without_hardware(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "report.json"
            smoke_test(destination)
            self.assertIn('"ok": true', destination.read_text(encoding="utf-8"))
