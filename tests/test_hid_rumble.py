# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from u2c_customfw.web.hid_rumble import HIDRumble,packet

class HIDRumbleTest(unittest.TestCase):
    def device(self,mode='cable_dinput',pid=0x301d):
        return SimpleNamespace(mode=mode,info=SimpleNamespace(vendor=0x2dc8,product=pid,transport='USB'))
    def test_targets_only_dinput_and_requires_unique_device(self):
        r=HIDRumble()
        for mode,pid,ok in [('cable_dinput',0x301b,True),('cable_dinput',0x301d,True),('dongle_paired',0x301c,True),('cable',0x310a,False),('boot',0x3208,False),('dongle',0x301c,False),('bluetooth',0x301b,False)]:
            with patch('u2c_customfw.web.hid_rumble.cli.find',return_value=[self.device(mode,pid)]):
                self.assertEqual(r.status()['available'],ok)
        with patch('u2c_customfw.web.hid_rumble.cli.find',return_value=[self.device(),self.device()]):
            self.assertFalse(r.status()['available'])
    def test_each_motor_and_final_stop_without_waiting_for_ack(self):
        for locality,values in [('default',(80,80)),('left',(80,0)),('right',(0,80))]:
            r=HIDRumble();ch=Mock()
            with patch.object(r,'target',return_value=self.device()),patch('u2c_customfw.web.hid_rumble.cli.open_channel',return_value=ch),patch.object(r.stop_event,'wait') as wait:
                self.assertTrue(r.rumble(locality)['ok'])
            self.assertEqual(ch.xfer.call_args_list[0].args,(packet(*values),0))
            self.assertEqual(ch.xfer.call_args_list[-1].args,(packet(0,0),0))
            wait.assert_called_once_with(.45);ch.close.assert_called_once()
    def test_interruption_stops_and_releases_channel(self):
        r=HIDRumble();ch=Mock()
        with patch.object(r,'target',return_value=self.device()),patch('u2c_customfw.web.hid_rumble.cli.open_channel',return_value=ch),patch.object(r.stop_event,'wait',side_effect=RuntimeError('interrupted')):
            self.assertFalse(r.rumble('left')['ok'])
        self.assertEqual(ch.xfer.call_args_list[-1].args,(packet(0,0),0));ch.close.assert_called_once()
        self.assertTrue(r.lock.acquire(False));r.lock.release()
    def test_invalid_unavailable_and_busy_do_not_send(self):
        r=HIDRumble()
        with patch.object(r,'target',return_value=None),patch('u2c_customfw.web.hid_rumble.cli.open_channel') as opened:
            self.assertFalse(r.rumble('bad')['ok']);self.assertFalse(r.rumble('left')['ok'])
            r.lock.acquire();self.assertFalse(r.rumble('left')['ok']);r.lock.release();opened.assert_not_called()

    def test_bluetooth_requires_authored_descriptor_and_stops_each_motor(self):
        from u2c_customfw.web import hid_rumble as module
        f=SimpleNamespace(mode='bluetooth',info=SimpleNamespace(vendor=0x2dc8,product=0x301b,transport='Bluetooth',handle=b'bt'))
        for descriptor,available in (((module.BLOBS/'btdmap.bin').read_bytes(),True),((module.BLOBS/'btdmap01.bin').read_bytes(),False),(b'',False)):
            device=Mock();device.get_report_descriptor.return_value=descriptor
            with patch.object(module.cli,'find',return_value=[f]),patch.object(module,'_hid',return_value=Mock(device=lambda:device)):
                self.assertEqual(HIDRumble().status()['available'],available)
            device.write.assert_not_called();device.close.assert_called_once()
        assert module.supports_bluetooth_report((module.BLOBS/'btdmap.bin').read_bytes().replace(bytes.fromhex('26 ff 00'),bytes.fromhex('27 ff 00 00 00')))
        for locality,left,right in (('default',80,80),('left',80,0),('right',0,80)):
            r=HIDRumble();device=Mock();device.write.side_effect=len
            with patch.object(r,'target',return_value=f),patch.object(module,'_hid',return_value=Mock(device=lambda:device)),patch.object(r.stop_event,'wait'):
                self.assertTrue(r.rumble(locality)['ok'])
            self.assertEqual(device.write.call_args_list[0].args,(module.bluetooth_packet(left,right),))
            self.assertEqual(device.write.call_args_list[-1].args,(module.bluetooth_packet(0,0),))
            device.close.assert_called_once()
