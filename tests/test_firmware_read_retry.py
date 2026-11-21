# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest.mock import Mock,patch,call
from types import SimpleNamespace
from u2c_customfw.protocol import app
from u2c_customfw.ops import probe
from u2c_customfw.web import server
from u2c_customfw.ops.plan import OpError
class FirmwareReadRetryTest(unittest.TestCase):
    def test_read_backoff_keeps_exact_address_and_echo_validation(self):
        ch=Mock();addr=0x1503e;n=3
        reply=bytearray(64);reply[:3]=bytes((2,5,0));reply[4]=5;reply[6:8]=n.to_bytes(2,'little');reply[14:18]=addr.to_bytes(4,'little');reply[18:21]=b'abc'
        ch.xfer.side_effect=[None,None,bytes(reply)]
        with patch.object(app.time,'sleep') as sleep:self.assertEqual(app.read(ch,addr,n),b'abc')
        self.assertEqual(sleep.call_args_list,[call(.02),call(.06)])
        self.assertEqual({c.args[0] for c in ch.xfer.call_args_list},{app._req(addr,n)})
    def test_ui_reopens_only_after_failed_dinput_read(self):
        f=SimpleNamespace(info=object(),mode='cable_dinput');first=Mock();second=Mock();fw=object()
        with patch.object(server.sys,'platform','win32'),patch.object(server.cli,'open_channel',side_effect=[first,second]) as opened,patch.object(server.probe,'read_firmware',side_effect=[OpError("The controller didn't answer at 0x1503e."),fw]) as read,patch.object(server.cli,'unit_backups',return_value=[]) as backups,patch.object(server.time,'sleep'):
            self.assertEqual(server.read_controller_details(f,None),(fw,[]))
        self.assertEqual(opened.call_count,2);first.close.assert_called_once();second.close.assert_called_once()
        read.assert_has_calls([call(first,'cable_dinput'),call(second,'cable_dinput')]);backups.assert_called_once()
    def test_failure_remains_bounded_and_receivers_are_not_retried(self):
        f=SimpleNamespace(info=object(),mode='cable_dinput')
        for failure,count in ((OpError('lost'),2),(probe.PairedDongle('receiver'),1)):
            with patch.object(server.sys,'platform','win32'),patch.object(server.cli,'open_channel',return_value=Mock()) as opened,patch.object(server.probe,'read_firmware',side_effect=failure),patch.object(server.time,'sleep'):
                with self.assertRaises(type(failure)):server.read_controller_details(f,None)
                self.assertEqual(opened.call_count,count)
