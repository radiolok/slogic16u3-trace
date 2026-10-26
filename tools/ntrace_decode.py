#!/usr/bin/env python3
"""RISC-V N-Trace decoder for usb3d captures (PIB, 4 bits): messages and, with the ELF, the program path.

Input: a capture from `usb3d.py cap` (.bin, little-endian 32-bit words) or a word-per-line .hex.

    ntrace_decode.py decode run.bin                     # messages and TCODE histogram
    ntrace_decode.py decode run.bin --elf trace.elf     # + program path, checked against the ELF

The path reconstruction handles branch trace (BTM: DirectBranch per taken branch) and branch history (HTM: HIST
bits). BE-U1000 specifics are handled: ResourceFull(HIST) arrives without TCODE and resets I-CNT without sending
it. With the demo firmware (tools/targets/beu1000) the decoder also checks the op_a -> op_b -> op_c call order.
"""
import argparse
import array
import os
import re
import subprocess
import sys

OBJDUMP = os.environ.get("RISCV_OBJDUMP", "riscv32-none-elf-objdump")


def load(path, nwords=0, skip=0):
    """Words of a capture: .bin (little-endian u32) or .hex (one word per line)."""
    if path.endswith(".hex"):
        w = [int(x, 16) for x in open(path).read().split()]
        return w[skip:skip + nwords] if nwords else w[skip:]
    with open(path, "rb") as f:
        f.seek(4 * skip)
        return array.array("I", f.read(4 * nwords) if nwords else f.read())


def nibbles(words):
    return [(x >> (4 * i)) & 0xF for x in words for i in range(8)]


# ---- nibble stream -> messages ----

def messages(nib):
    """N-Trace messages from a nibble stream (low nibble first). Idle is nibble F. Idle may have odd length, so
    alignment restarts after each idle: a message starts at the first nibble after F with MSEO (low 2 bits) = 00.
    Returns ([(nibble index, bytes)], nibbles outside messages)."""
    msgs, n = [], len(nib)
    bad = 0
    i = nib.index(0xF) if 0xF in nib else n  # before the first idle: tail of a message started earlier
    while i < n:
        if nib[i] == 0xF:
            i += 1
            continue
        if nib[i] & 3:
            bad += 1  # not a message start: middle of one cut by the capture start, or an error
            i += 1
            continue
        start, cur = i, []
        while i + 1 < n:
            b = nib[i] | nib[i + 1] << 4
            cur.append(b)
            i += 2
            if b & 3 == 3:
                break
        else:
            break  # cut at the end of the capture
        msgs.append((start, bytes(cur)))
    return msgs, bad


# Message fields (N-Trace 1.0, "Fields in Messages"): fixed (name, bits) or variable (name, None).
# BE-U1000 has no SRC field (trTeInhibitSrc = 1, read-only).
LAYOUT = {
    2: ("Ownership", [("PROCESS", None)]),
    3: ("DirectBranch", [("ICNT", None)]),
    4: ("IndirectBranch", [("BTYPE", 2), ("ICNT", None), ("UADDR", None)]),
    8: ("Error", [("ETYPE", 4), ("ECODE", None)]),
    9: ("ProgTraceSync", [("SYNC", 4), ("ICNT", None), ("FADDR", None)]),
    11: ("DirectBranchSync", [("SYNC", 4), ("ICNT", None), ("FADDR", None)]),
    12: ("IndirectBranchSync", [("SYNC", 4), ("BTYPE", 2), ("ICNT", None), ("FADDR", None)]),
    27: ("ResourceFull", [("RCODE", 4), ("RDATA", None)]),
    28: ("IndirectBranchHist", [("BTYPE", 2), ("ICNT", None), ("UADDR", None), ("HIST", None)]),
    29: ("IndirectBranchHistSync", [("SYNC", 4), ("BTYPE", 2), ("ICNT", None), ("FADDR", None), ("HIST", None)]),
    30: ("RepeatBranch", [("BCNT", None)]),
    33: ("ProgTraceCorrelation", [("EVCODE", 4), ("CDF", 2), ("ICNT", None), ("HIST", None)]),
}
TCODES = {t: n for t, (n, _) in LAYOUT.items()}
EVCODE_NAMES = {0: "debug entry", 1: "sleep", 4: "trace disabled"}
SYNC_NAMES = {0: "external trigger", 1: "reset exit", 2: "periodic", 3: "debug exit", 4: "I-CNT overflow",
              5: "trace enabled", 6: "event", 7: "after FIFO overflow", 9: "sleep exit"}


def parse_msg(msg):
    """Message -> (TCODE, {field: value}). MDO bits are concatenated LSB first; a variable field ends at a byte
    with MSEO = 01 or 11 (TSTAMP, if present, is the last one)."""
    bits, ends, nb = 0, [], 0
    for b in msg:
        bits |= (b >> 2) << nb
        nb += 6
        if b & 3:
            ends.append(nb)
    tcode = bits & 0x3F
    _, lay = LAYOUT.get(tcode, (None, []))
    pos, out, k = 6, {}, 0
    if (bits & 0xF) in (0, 1) and len(ends) == 1 and tcode not in (16, 32) and (tcode not in LAYOUT or tcode == 33):
        # BE-U1000 sends ResourceFull without TCODE: RCODE (4 bits) and RDATA as one field. The low bits of the
        # first byte are RCODE (0: I-CNT full, 1: HIST full) and data follows, so the "TCODE" reads as 1/17/33/49
        # or 0/16/48. A real ProgTraceCorrelation (33) has two fields.
        return 27, {"RCODE": bits & 0xF, "RDATA": bits >> 4, "NOTCODE": 1}
    for fname, w in lay:
        if w is not None:
            out[fname] = bits >> pos & ((1 << w) - 1)
            pos += w
        else:
            if k >= len(ends):
                if not (tcode == 33 and fname == "HIST"):  # in BTM ProgTraceCorrelation has no HIST
                    out["_err"] = f"no end of field {fname}"
                break
            out[fname] = bits >> pos & ((1 << (ends[k] - pos)) - 1)
            pos = ends[k]
            k += 1
    if k < len(ends):
        out["TSTAMP"] = bits >> pos & ((1 << (ends[k] - pos)) - 1)
    return tcode, out


def fmt_msg(tcode, f):
    s = TCODES.get(tcode, f"TCODE{tcode}")
    for k, v in f.items():
        if k == "NOTCODE":
            s += " [no TCODE]"
        elif k in ("FADDR", "UADDR"):
            s += f" {k}={v << 1:#x}"
        elif k == "EVCODE":
            s += f" EVCODE={v} ({EVCODE_NAMES.get(v, '?')})"
        elif k == "SYNC":
            s += f" SYNC={v} ({SYNC_NAMES.get(v, '?')})"
        else:
            s += f" {k}={v}"
    return s


# ---- program path from the ELF ----

COND = {"beq", "bne", "blt", "bge", "bltu", "bgeu", "beqz", "bnez", "blez", "bgez", "bltz", "bgtz",
        "bgt", "ble", "bgtu", "bleu", "c.beqz", "c.bnez"}
DJUMP = {"j", "jal", "c.j", "c.jal"}
IJUMP = {"jalr", "jr", "ret", "c.jr", "c.jalr", "mret"}


def disasm(elf, objdump=OBJDUMP):
    """{address: (size, mnemonic, direct branch target or None, function, operands)}."""
    out = subprocess.run([objdump, "-d", elf], capture_output=True, text=True, check=True).stdout
    dis, func = {}, "?"
    for line in out.splitlines():
        m = re.match(r"^([0-9a-f]+) <(.+)>:$", line)
        if m:
            func = m[2]
            continue
        m = re.match(r"^\s*([0-9a-f]+):\s+([0-9a-f]+)\s+(\S+)\s*(.*)$", line)
        if not m:
            continue
        addr, enc, mn, ops = int(m[1], 16), m[2], m[3], m[4]
        tgt = None
        if mn in COND or mn in DJUMP:
            t = re.search(r"\b([0-9a-f]{6,})\s*<", ops)
            tgt = int(t[1], 16) if t else None
        dis[addr] = (len(enc) // 2, mn, tgt, func, ops)
    return dis


def hist_bits(h):
    """HIST -> conditional branch outcomes in order (the top set bit is the stop bit)."""
    n = h.bit_length() - 1
    return [(h >> k) & 1 for k in range(n - 1, -1, -1)]


class Flow:
    """Program path from messages. I-CNT counts half-words since the previous message; in BTM a taken conditional
    branch is a DirectBranch and not-taken ones are silent; in HTM every conditional outcome is a HIST bit
    (IndirectBranchHist or ResourceFull). rows: [address, message index, action]."""

    def __init__(self, dis, htm=False):
        self.dis, self.htm, self.pc, self.ref = dis, htm, None, 0
        self.func_start = set()
        self.ninstr, self.errors, self.calls, self.rows = 0, [], [], []
        self.pend_icnt, self.bits, self.last_branch, self.lost = 0, [], None, 0

    def step(self, pc):
        d = self.dis.get(pc)
        if d is None:
            raise ValueError(f"no instruction at {pc:#x}")
        return d

    def cond(self, pc, size, tgt):
        t = self.bits.pop(0)
        return (tgt, f"HIST 1: taken -> {tgt:08x}") if t else (pc + size, "HIST 0: not taken")

    def walk_bits(self, mi):
        """BE-U1000: when HIST fills up (ResourceFull without TCODE) the encoder resets I-CNT without sending it.
        The block end follows from HIST itself: walk until the last bit is used; the block ends at that
        conditional branch and the next message's I-CNT counts from it."""
        if self.pend_icnt:
            raise ValueError("I-CNT from ResourceFull before ResourceFull(HIST)")
        while self.bits:
            pc = self.pc
            size, mn, tgt = self.step(pc)[:3]
            if mn in COND:
                nxt, act = self.cond(pc, size, tgt)
            elif mn in DJUMP:
                nxt, act = tgt, f"direct -> {tgt:08x}"
            elif mn in IJUMP:
                raise ValueError(f"indirect jump {pc:#x} inside ResourceFull(HIST)")
            else:
                nxt, act = pc + size, "next"
            self.rows.append([pc, mi, act])
            self.ninstr += 1
            self.pc = nxt

    def walk(self, icnt, mi):
        """Walk icnt half-words (plus I-CNT pending from ResourceFull) from self.pc; returns the last
        instruction of the block."""
        left, last = icnt + self.pend_icnt, None
        self.pend_icnt = 0
        while left > 0:
            pc = self.pc
            d = self.step(pc)
            size, mn, tgt = d[:3]
            left -= size // 2
            if left < 0:
                raise ValueError(f"I-CNT ends inside instruction {pc:#x}")
            if mn in COND:
                if self.htm:
                    if not self.bits:
                        raise ValueError(f"conditional branch {pc:#x} with no HIST bits left")
                    nxt, act = self.cond(pc, size, tgt)
                else:
                    nxt, act = pc + size, "not taken"
            elif mn in DJUMP:
                nxt, act = tgt, f"direct -> {tgt:08x}"
            elif mn in IJUMP:
                if left > 0:
                    raise ValueError(f"indirect jump {pc:#x} inside a block")
                nxt, act = None, "indirect"
            else:
                nxt, act = pc + size, "next"
            self.rows.append([pc, mi, act])
            self.ninstr += 1
            last = (pc, d)
            self.pc = nxt
        return last

    def msg(self, tcode, f, mi=-1):
        try:
            self._msg(tcode, f, mi)
        except ValueError as e:
            self.errors.append(f"message {mi}: {e}")
            self.pc, self.bits, self.pend_icnt = None, [], 0  # wait for a sync

    def _msg(self, tcode, f, mi):
        if tcode == 8:  # Error: messages lost, continue from the next sync
            self.lost += 1
            self.calls.append("|")
            self.pc, self.bits, self.pend_icnt = None, [], 0
            return
        if "FADDR" in f:
            addr = f["FADDR"] << 1
            if "HIST" in f:
                self.bits += hist_bits(f["HIST"])
            if self.pc is not None and f.get("ICNT"):
                _, d = self.walk(f["ICNT"], mi)
                nxt = d[2] if tcode == 11 else self.pc
                if tcode in (9, 11) and nxt != addr:
                    self.errors.append(f"message {mi}: sync, walked to {nxt or 0:#x} but F-ADDR is {addr:#x}")
                if tcode != 9:
                    self.rows[-1][2] += f" -> F-ADDR {addr:08x}"
            if self.bits:
                self.errors.append(f"message {mi}: {len(self.bits)} HIST bits left after sync")
            self.pc = self.ref = addr
            self.bits, self.pend_icnt = [], 0
            if addr in self.func_start:  # trace turned on at a function entry (trace-on trigger)
                self.calls.append(self.dis[addr][3])
            return
        if tcode == 27:  # ResourceFull
            if self.pc is None:
                return  # nothing to attach to before the first sync
            if f["RCODE"] == 0:
                self.pend_icnt += f["RDATA"]
            elif f["RCODE"] in (1, 2):
                self.bits += hist_bits(f["RDATA"]) * (1 + (f.get("TSTAMP", 0) if f["RCODE"] == 2 else 0))
                if f.get("NOTCODE"):
                    self.walk_bits(mi)
            return
        if tcode == 30:  # RepeatBranch: repeat the previous branch message B-CNT times
            if self.last_branch:
                for _ in range(f["BCNT"]):
                    self._msg(*self.last_branch, mi)
            return
        if self.pc is None:
            return
        if tcode == 33:  # trace stopped: I-CNT and HIST up to the stop point, then wait for a sync
            if "HIST" in f:
                self.bits += hist_bits(f["HIST"])
            if f["ICNT"]:
                self.walk(f["ICNT"], mi)
                self.rows[-1][2] += ", trace stopped"
            self.pc, self.bits = None, []
            self.calls.append("|")
            return
        if tcode == 3:
            a, d = self.walk(f["ICNT"], mi)
            if d[1] not in COND:
                raise ValueError(f"DirectBranch, but the last instruction {a:#x} is {d[1]}")
            self.pc = d[2]
            self.rows[-1][2] = f"taken -> {d[2]:08x}"
            self.last_branch = (tcode, f)
        elif tcode in (4, 28):
            if tcode == 28:
                self.bits += hist_bits(f["HIST"])
            a, d = self.walk(f["ICNT"], mi)
            new = (f["UADDR"] << 1) ^ self.ref
            if f["BTYPE"] == 0 and d[1] not in IJUMP:
                raise ValueError(f"IndirectBranch, but the last instruction {a:#x} is {d[1]}")
            if self.bits:
                raise ValueError(f"{len(self.bits)} unused HIST bits")
            self.pc = self.ref = new
            self.rows[-1][2] = f"indirect -> {new:08x} (U-ADDR)"
            self.last_branch = (tcode, f)
            self.calls.append(self.dis.get(new, (0, "", None, "?"))[3] + ("" if new in self.func_start else "+"))


def check_pattern(calls):
    """Demo firmware (tools/targets/beu1000/fw): every loop pass calls ops[i % 3], so op_a, op_b, op_c must follow
    each other without gaps. Returns (calls, order violations)."""
    ops = [c for c in calls if c in ("op_a", "op_b", "op_c", "|")]  # "|" marks a break after Error
    order = {"op_a": 0, "op_b": 1, "op_c": 2}
    gaps = sum(1 for x, y in zip(ops, ops[1:]) if "|" not in (x, y) and (order[x] + 1) % 3 != order[y])
    return len([c for c in ops if c != "|"]), gaps


def report(nib, elf=None, objdump=OBJDUMP, limit=40):
    msgs, bad = messages(nib)
    parsed, hist = [], {}
    for start, m in msgs:
        t, f = parse_msg(m)
        parsed.append((start, m, t, f))
        hist[t] = hist.get(t, 0) + 1
    print(f"nibbles {len(nib)}, idle {sum(1 for x in nib if x == 0xF)}, messages {len(msgs)}, "
          f"outside messages {bad}")
    for t, c in sorted(hist.items()):
        print(f"  TCODE {t:2} {TCODES.get(t, '?'):22} {c}")
    errs = [i for i, (_, _, _, f) in enumerate(parsed) if "_err" in f]
    if not elf:
        for start, m, t, f in parsed[:limit]:
            print(f"  @{start:6} {m.hex(' '):24} {fmt_msg(t, f)}")
        ok = bool(msgs) and not errs
        print(f"malformed messages {len(errs)}")
        print("PASS" if ok else "FAIL")
        return ok
    dis = disasm(elf, objdump)
    htm = sum(t in (28, 29) for _, _, t, _ in parsed) > sum(t == 3 for _, _, t, _ in parsed)
    fl = Flow(dis, htm=htm)
    fl.func_start = {a for a, d in dis.items() if dis.get(a - 2, (0, 0, 0, None))[3] != d[3]
                     and dis.get(a - 4, (0, 0, 0, None))[3] != d[3]}
    for i, (start, m, t, f) in enumerate(parsed):
        fl.msg(t, f, i)
        if i < limit:
            tail = ""
            if fl.pc is not None and t in (3, 4, 9, 11, 12, 28, 29):
                d = dis.get(fl.pc)
                tail = f"-> {fl.pc:#x} <{d[3] if d else '?'}>"
            print(f"  @{start:6} {m.hex(' '):24} {fmt_msg(t, f):58} {tail}")
    n, gaps = check_pattern(fl.calls)
    nbytes = sum(len(m) for _, m in msgs)
    print(f"trace bytes {nbytes}, instructions {fl.ninstr}: {8 * nbytes / max(1, fl.ninstr):.2f} bits per "
          f"instruction; Error messages {fl.lost}; path errors {len(fl.errors)}")
    for e in fl.errors[:10]:
        print("  !", e)
    if n:
        print(f"op_a/op_b/op_c calls {n}, order violations {gaps}")
    ok = bool(msgs) and not fl.errors and not gaps
    print("PASS: program path matches the ELF" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("decode")
    p.add_argument("file", help=".bin from usb3d.py cap or .hex (a word per line)")
    p.add_argument("--words", type=int, default=1 << 20, help="words to decode (0 = all)")
    p.add_argument("--skip", type=int, default=0, help="words to skip from the start")
    p.add_argument("--elf", help="target firmware, for program path reconstruction")
    p.add_argument("--objdump", default=OBJDUMP, help="RISC-V objdump (or $RISCV_OBJDUMP)")
    p.add_argument("--limit", type=int, default=40, help="messages to print")
    a = ap.parse_args()
    ok = report(nibbles(load(a.file, a.words, a.skip)), a.elf, a.objdump, a.limit)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
