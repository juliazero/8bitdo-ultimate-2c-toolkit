import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from tools import dongle_xinput_interfaces as d


class XInputInterfacesTest(TestCase):
    def test_only_exact_vid_pid_are_enumerated(self):
        h = Mock()
        h.enumerate.side_effect = [[{'path': b'\\\\?\\hid#abc', 'usage_page': 1,
                                     'usage': 5, 'release_number': 0x103}],
                                   [{'path': b'vendor', 'usage_page': 0xff7a,
                                     'usage': 0, 'release_number': 0x103}],
                                   [{'path': b'MI_02', 'usage_page': 0xff7a,
                                     'usage': 0, 'release_number': 0x114, 'interface_number': 2}]]
        got = d.hid_interfaces(h)
        self.assertEqual([(x['vid'], x['pid']) for x in got], [('045e', '028e'), ('2dc8', '301c'), ('2dc8', '310a')])
        self.assertEqual(got[2]['usage_page'], 'ff7a')
        self.assertEqual(h.enumerate.call_args_list, [((0x045e, 0x028e),), ((0x2dc8, 0x301c),), ((0x2dc8, 0x310a),)])

    def test_pnp_array_and_empty_response(self):
        run = Mock(return_value=SimpleNamespace(returncode=0, stdout='[{"service":"xusb22"}]'))
        self.assertEqual(d.pnp_interfaces(run)['devices'][0]['service'], 'xusb22')
        run.return_value.stdout = '[]'
        self.assertEqual(d.pnp_interfaces(run)['devices'], [])

    def test_nonzero_pnp_is_nonfatal(self):
        run = Mock(return_value=SimpleNamespace(returncode=1, stdout=''))
        self.assertEqual(d.pnp_interfaces(run)['devices'], [])
        self.assertIn('error', d.pnp_interfaces(run))

    def test_gamepad_is_not_mistaken_for_vendor(self):
        h = Mock()
        h.enumerate.side_effect = [[{'usage_page': 1, 'usage': 5}], [], []]
        run = Mock(return_value=SimpleNamespace(returncode=0, stdout='[]'))
        got = d.diagnose(h, run)
        self.assertEqual(got['xinput_vendor_hid_candidates'], [])
        self.assertIn('does not provide access', got['interpretation'])

    def test_no_device_modifying_calls(self):
        h = Mock()
        h.enumerate.side_effect = [[], [], []]
        run = Mock(return_value=SimpleNamespace(returncode=0, stdout='[]'))
        d.diagnose(h, run)
        h.device.assert_not_called()


class FirmwareDescriptorTest(TestCase):
    def test_firmware_metadata_does_not_claim_os_visibility(self):
        hid = Mock()
        hid.enumerate.side_effect = [[], [], []]
        run = Mock(return_value=SimpleNamespace(returncode=0, stdout='[]'))
        result = d.diagnose(hid, run)
        info = result['firmware_descriptor_expected']
        self.assertEqual(info['interface_count'], 3)
        self.assertIn('0x83', info['interface_2'])
        self.assertIn('does not prove Windows', info['note'])
        self.assertEqual(result['xinput_vendor_hid_candidates'], [])
