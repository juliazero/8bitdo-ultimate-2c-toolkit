# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from tools.check_ring_hold import check

class RingHoldInstructionTest(unittest.TestCase):
    def test_compiled_tc32_hold_and_existing_tasks(self):
        self.assertTrue(check())
