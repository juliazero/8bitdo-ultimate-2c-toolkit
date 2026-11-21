# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from tools.switch2_pairing import PairingSession,aes128_block

ADDR=bytes.fromhex("98e255c21688")
ADDRESS=bytes.fromhex("15910101000e0000000281eb3aebf14880eb3aebf148")
KEY=bytes.fromhex("1591010400110000003503e92982877124bea80c664615834b")
CHALLENGE=bytes.fromhex("1591010200110000006fc6df8ad8fedf15bb8c15e91f320544")
COMMIT=bytes.fromhex("159101030001000000")

class Switch2PairingTest(unittest.TestCase):
    def test_aes_nist_vectors(self):
        key=bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        for plain,cipher in (("6bc1bee22e409f96e93d7e117393172a","3ad77bb40d7a3660a89ecaf32466ef97"),
                             ("ae2d8a571e03ac9c9eb76fac45af8e51","f5d3d58503b9699de785895a96fdbaaf"),
                             ("30c81c46a35ce411e5fbc1191a0a52ef","43b1cd7f598ece23881b00e3ed030688"),
                             ("f69f2445df4f9b17ad2b417be66c3710","7b0c785e27e8ad3f8223207104725dd4")):
            self.assertEqual(aes128_block(key,bytes.fromhex(plain)).hex(),cipher)
    def test_captured_pairing_exchange_and_explicit_commit(self):
        s=PairingSession(ADDR)
        self.assertEqual(s.handle(ADDRESS).hex(),"15010101107800000104018816c255e298")
        self.assertEqual(s.handle(KEY).hex(),"1501010410780000015cf6ee792cdf05e1ba2b6325c41a5f10")
        expected="150101021078000001134c97f511b9b6dd4d86fd40f536e9ed"
        self.assertEqual(s.handle(CHALLENGE).hex(),expected)
        self.assertEqual(s.handle(CHALLENGE).hex(),expected)  # retransmission
        self.assertEqual(s.bonds,())
        self.assertEqual(s.handle(COMMIT).hex(),"150101031078000001")
        self.assertEqual(tuple(b.host.hex() for b in s.bonds),("48f1eb3aeb81","48f1eb3aeb80"))
        self.assertEqual(len(s.bonds[0].ltk_wire),16)
        self.assertNotIn(s.bonds[0].ltk_wire.hex(),repr(s.bonds[0]))
        s.disconnect();self.assertEqual(s.stage,"idle");self.assertEqual(len(s.bonds),2)
    def test_invalid_order_and_disconnect_do_not_create_bonds(self):
        for request in (KEY,CHALLENGE,COMMIT):
            s=PairingSession(ADDR)
            with self.assertRaises(ValueError):s.handle(request)
            self.assertEqual(s.bonds,())
        s=PairingSession(ADDR);s.handle(ADDRESS);s.handle(KEY);s.disconnect()
        with self.assertRaises(ValueError):s.handle(COMMIT)
        self.assertEqual(s.bonds,())
    def test_bad_frames_cannot_destroy_committed_pairing(self):
        s=PairingSession(ADDR)
        for request in (ADDRESS,KEY,CHALLENGE,COMMIT):s.handle(request)
        committed=s.bonds
        self.assertEqual(s.handle(COMMIT),bytes.fromhex("150101031078000001"))
        self.assertEqual(s.bonds,committed)
        for request in (bytes(7),ADDRESS[:-1],ADDRESS+bytes(1),bytes.fromhex("15910101000200000003"),CHALLENGE,bytes.fromhex("1591010500000000")):
            with self.assertRaises(ValueError):s.handle(request)
            self.assertEqual(s.bonds,committed)
        query=bytes.fromhex("1591010100000000")
        self.assertEqual(s.handle(query)[8:],bytes.fromhex("0104018816c255e298"))
        self.assertEqual(s.bonds,committed)

    def test_switch2connect_repeated_adapter_address(self):
        session=PairingSession(ADDR)
        host=bytes.fromhex('1371da7d1a00')
        request=bytes.fromhex('15910101000e00000002')+host+host
        self.assertEqual(session.handle(request)[0:4],bytes.fromhex('15010101'))
        for packet in (KEY,CHALLENGE,COMMIT):session.handle(packet)
        self.assertEqual(len(session.bonds),2)
        self.assertEqual(session.bonds[0],session.bonds[1])
        session.disconnect()
        self.assertEqual(session.bonds[0].host,host[::-1])
