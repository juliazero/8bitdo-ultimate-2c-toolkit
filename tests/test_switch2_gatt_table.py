# SPDX-License-Identifier: GPL-3.0-or-later
import json,struct,tempfile,unittest,uuid
from pathlib import Path
from tools import switch2_layout as layout
from tools.build_switch2_gatt import build,BASE

class Pro2GattTableTest(unittest.TestCase):
    def test_vendor_uuid_order_and_real_callback_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            (folder/"pairing-prototype.json").write_text(json.dumps({"symbols":{"y_att_write":0x1a200}}))
            result=build(folder);data=(folder/"gatt-prototype.bin").read_bytes()
        self.assertEqual(struct.unpack_from("<I",data)[0],47)
        def entry(handle):return struct.unpack_from("<HBB5I",data,handle*24)
        gamepad=entry(0xe)
        self.assertEqual(gamepad[2:4],(16,63))
        uuid_ptr=gamepad[4]-BASE
        self.assertEqual(data[uuid_ptr:uuid_ptr+16],uuid.UUID("7492866c-ec3e-4619-8258-32755ffcc0f8").bytes[::-1])
        command=entry(0x14)
        self.assertEqual(command[1],2)
        self.assertEqual(command[6],0x1a201)
        self.assertEqual(entry(0x1f)[5],layout.Y_REPLY_CCC)
        self.assertEqual(entry(5)[1],2)
        self.assertEqual(entry(5)[3],2)
        self.assertEqual(entry(5)[5],layout.Y_BOOTSTRAP)
        self.assertEqual(entry(0x10)[1],3)
        self.assertEqual(entry(0x10)[3],2)
        self.assertEqual(entry(0x10)[5],layout.Y_RATE)
        self.assertEqual(entry(0x16)[6],0x1a201)
        self.assertEqual(entry(0x12)[1],2)
        self.assertEqual(entry(0x12)[6],0x1a201)
        for handle in (0x18,0x2a):self.assertEqual(entry(handle)[1],0)
        self.assertFalse(result["installable"])
        self.assertTrue(result["offline_only"])
