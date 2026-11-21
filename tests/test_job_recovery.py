# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from u2c_customfw.web import jobs


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        logger = patch.object(jobs, "get_logger", return_value=Mock())
        logger.start()
        self.addCleanup(logger.stop)

    def test_result_is_available_after_disconnect_and_a_later_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = jobs.Runner(Path(tmp))
            gate = threading.Event()
            def action(args, store):
                gate.wait(5)
                args.say("Backup saved")
            with patch.dict(jobs.ACTIONS, backup=action):
                first = runner.start("backup")
                self.assertEqual(runner.status(first), {"running": True, "result": None})
                gate.set()
                with runner.cond:
                    self.assertTrue(runner.cond.wait_for(lambda: not runner.running, timeout=5))
                result = runner.status(first)
                self.assertEqual(result["result"], {"type": "done", "message": "Backup saved", "unchanged": False})
                second = runner.start("backup")
                self.assertNotEqual(first, second)
                self.assertEqual(runner.status(first), result)
                self.assertIsNone(runner.status("another-session"))
                with runner.cond:
                    self.assertTrue(runner.cond.wait_for(lambda: not runner.running, timeout=5))

    def test_failed_job_is_not_recovered_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = jobs.Runner(Path(tmp))
            def action(args, store):
                raise ValueError("read failed")
            with patch.dict(jobs.ACTIONS, backup=action):
                job_id = runner.start("backup")
                with runner.cond:
                    self.assertTrue(runner.cond.wait_for(lambda: not runner.running, timeout=5))
            self.assertEqual(runner.status(job_id)["result"], {"type": "error", "message": "ValueError: read failed"})
