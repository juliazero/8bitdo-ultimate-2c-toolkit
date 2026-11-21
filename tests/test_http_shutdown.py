# SPDX-License-Identifier: GPL-3.0-or-later
import socket
import sys
import unittest
from unittest.mock import Mock, call, patch

from u2c_customfw.web.server import LocalHTTPServer


class ShutdownTest(unittest.TestCase):
    def test_windows_half_closes_before_waiting_for_peer_then_releases_socket(self):
        server = object.__new__(LocalHTTPServer)
        request = Mock()
        request.recv.side_effect = [b"remaining body", b""]
        with patch.object(sys, "platform", "win32"):
            server.shutdown_request(request)
        self.assertEqual(request.mock_calls[0], call.shutdown(socket.SHUT_WR))
        self.assertEqual(request.recv.call_count, 2)
        self.assertEqual(request.mock_calls[-1], call.close())
        self.assertTrue(all(0 < entry.args[0] <= 2.0 for entry in request.settimeout.call_args_list))

    def test_timeout_still_releases_socket(self):
        server = object.__new__(LocalHTTPServer)
        request = Mock()
        request.recv.side_effect = TimeoutError()
        with patch.object(sys, "platform", "win32"):
            server.shutdown_request(request)
        request.close.assert_called_once()

    def test_other_platforms_keep_standard_shutdown(self):
        server = object.__new__(LocalHTTPServer)
        request = Mock()
        with patch.object(sys, "platform", "linux"):
            server.shutdown_request(request)
        request.recv.assert_not_called()
        request.close.assert_called_once()
