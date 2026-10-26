"""Unit tests of the trace decoders, no hardware or toolchain needed:  python3 -m unittest discover tools/test"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ntrace_decode as nt  # noqa: E402
import tpiu_decode as tp  # noqa: E402


def words_of(nibs):
    """Pack nibbles into usb3d words: 8 nibbles per word, the first one in [3:0]."""
    nibs = nibs + [0xF] * (-len(nibs) % 8)
    return [sum(nibs[i + k] << 4 * k for k in range(8)) for i in range(0, len(nibs), 8)]


class Tpiu(unittest.TestCase):
    def test_itm_counter(self):
        for shift in (0, 1, 3):  # capture may start at any nibble
            words = words_of([0xF] * shift + tp.encode(nvals=160))
            off, data, st = tp.tpiu(tp.nibbles(words))
            vals, _ = tp.itm(bytes(data[1]))
            self.assertEqual(len(vals), 160)
            self.assertEqual(vals[0], 0x12345670)
            self.assertEqual(tp.gaps_of(vals), [])

    def test_itm_packets(self):
        b = bytes([0, 0, 0, 0, 0, 0x80, 0x03, 0x78, 0x56, 0x34, 0x12, 0x70, 0x09, 0xAB])
        vals, st = tp.itm(b)
        self.assertEqual(vals, [0x12345678])
        self.assertEqual((st["sync"], st["overflow"], st["other"]), (1, 1, 1))
        kinds = [d for _, _, d in tp.itm_packets(b)]
        self.assertEqual(kinds[0], "sync")
        self.assertIn("stimulus port 0: 0x12345678", kinds[1])

    def test_etm_packets(self):
        b = bytes([0, 0, 0, 0, 0, 0x80,                 # A-sync
                   0x08, 0x20, 0x01, 0x01, 0x00, 0x08,  # I-sync, trace enabled, 0x08000101 (Thumb)
                   0x8C,                                # P-header format 1: EEE
                   0x41])                               # branch address, 1 byte: bits [6:0] = 0x40
        pk = tp.etm_packets(b)
        self.assertEqual([p["type"] for p in pk], ["A-sync", "I-sync", "P-header", "branch"])
        self.assertEqual(pk[1]["addr"], 0x08000100)
        self.assertEqual(pk[1]["reason"], "trace enabled")
        self.assertEqual(pk[2]["atoms"], "EEE")
        self.assertEqual(pk[3]["addr"], 0x08000140)

    def test_etm_instructions(self):
        dis = {0x100: (2, "movs", None, None, "f"), 0x102: (2, "b.n", "direct", 0x108, "f"),
               0x108: (2, "bx", "indirect", None, "f")}
        pk = [{"type": "I-sync", "addr": 0x100, "reason": "periodic"}, {"type": "P-header", "atoms": "EEE"}]
        rows, st = tp.etm_instructions(pk, dis)
        self.assertEqual([r[0] for r in rows if isinstance(r[0], int)], [0x100, 0x102, 0x108])
        self.assertEqual(st["lost"], 0)


def encode_msg(fields):
    """N-Trace message bytes from [(value, bits or None)]: fixed fields, or variable ones (None) that end a byte
    with MSEO 01; the last byte gets MSEO 11."""
    bits, nb, ends = 0, 0, []
    for v, w in fields:
        if w is None:
            w = max(v.bit_length(), 1)
            w += -(nb + w) % 6
            bits |= v << nb
            nb += w
            ends.append(nb // 6 - 1)
        else:
            bits |= v << nb
            nb += w
    out = []
    for i in range(nb // 6):
        mseo = 3 if i == ends[-1] else 1 if i in ends else 0
        out.append((bits >> 6 * i & 0x3F) << 2 | mseo)
    return bytes(out)


def stream(msgs):
    nib = [0xF] * 3
    for m in msgs:
        for b in m:
            nib += [b & 0xF, b >> 4]
        nib += [0xF] * 2
    return nib


class NTrace(unittest.TestCase):
    def test_messages(self):
        sync = encode_msg([(9, 6), (5, 4), (0x12, None), (0x20008, None)])
        hist = encode_msg([(28, 6), (0, 2), (7, None), (0x15, None), (0b1101, None)])
        rfull = encode_msg([(1, 4), (0b101, None)])  # BE-U1000: ResourceFull(HIST) without TCODE
        msgs, bad = nt.messages(stream([sync, hist, rfull]))
        self.assertEqual(bad, 0)
        self.assertEqual([m for _, m in msgs], [sync, hist, rfull])
        t, f = nt.parse_msg(sync)
        self.assertEqual((t, f["SYNC"], f["ICNT"], f["FADDR"] << 1), (9, 5, 0x12, 0x40010))
        t, f = nt.parse_msg(hist)
        self.assertEqual((t, f["BTYPE"], f["ICNT"], f["UADDR"], f["HIST"]), (28, 0, 7, 0x15, 0b1101))
        self.assertEqual(nt.hist_bits(f["HIST"]), [1, 0, 1])
        t, f = nt.parse_msg(rfull)
        self.assertEqual((t, f["RCODE"], f["RDATA"], f.get("NOTCODE")), (27, 1, 0b101, 1))

    def test_flow_btm(self):
        # 0x100 addi; 0x104 beq -> 0x10c; 0x108 addi; 0x10c jalr (returns to 0x100)
        dis = {0x100: (4, "addi", None, "f", ""), 0x104: (4, "beq", 0x10C, "f", ""),
               0x108: (4, "addi", None, "f", ""), 0x10C: (4, "jalr", None, "f", "")}
        fl = nt.Flow(dis)
        fl.msg(9, {"SYNC": 5, "ICNT": 0, "FADDR": 0x100 >> 1}, 0)   # ProgTraceSync at 0x100
        fl.msg(3, {"ICNT": 4}, 1)                                   # DirectBranch: beq taken
        fl.msg(4, {"BTYPE": 0, "ICNT": 2, "UADDR": 0}, 2)           # IndirectBranch: jalr -> 0x100
        self.assertEqual(fl.errors, [])
        self.assertEqual([r[0] for r in fl.rows], [0x100, 0x104, 0x10C])
        self.assertEqual(fl.pc, 0x100)


if __name__ == "__main__":
    unittest.main()
