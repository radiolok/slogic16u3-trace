#!/usr/bin/env python3
"""ARM TPIU decoder for usb3d captures: formatter frames -> ITM and ETMv3 (Cortex-M3/M4).

Input: a capture from `usb3d.py cap` (.bin, little-endian 32-bit words) or a testbench dump (.hex, a word per
line). Each byte of a word is one TRACECLK cycle, low nibble first, i.e. a 4-bit TPIU port stream.

    tpiu_decode.py decode run.bin                       # TPIU sync, frames, ITM stimulus port 0 counter check
    tpiu_decode.py show run.bin --elf fw.elf -o out.txt # ITM and ETM packets, executed instructions
    tpiu_decode.py encode --out stream.hex              # synthetic stream (ITM counter) for rtl/sim/tb_tpiu.v

ETM decoding follows the rules of OpenCSD (decoder/source/etmv3): a P-header atom is one instruction
(E executed, N failed its condition); a branch address packet follows the atom of an indirect branch and is
compressed against the previous address. Direct branch targets come from the ELF (objdump), so branch
broadcasting is not needed. The decoder is plain Python: ~4 s per 64K words; split long captures with
--skip/--words.
"""
import argparse
import array
import os
import re
import subprocess
import sys

OBJDUMP = os.environ.get("ARM_OBJDUMP", "arm-none-eabi-objdump")
SYNC = b"\xff\xff\xff\x7f"


# ---- capture ----

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


def remap(samples, lanes):
    """Bit i of a sample = lane TRACED<lanes[i]> (wiring fix-up); "0123" = as wired."""
    if lanes == "0123":
        return samples
    return [sum((x >> int(c) & 1) << i for i, c in enumerate(lanes)) for x in samples]


# ---- TPIU ----

def to_bytes(samples, width, off):
    bits = [(s >> b) & 1 for s in samples for b in range(width)]
    bits = bits[off:]
    return bytes(sum(bits[8 * i + b] << b for b in range(8)) for i in range(len(bits) // 8))


def frames(hw, st):
    """Formatter frames (16 bytes) from a half-word stream: {ID: bytes}."""
    data, cur = {}, None
    b = b"".join(x.to_bytes(2, "little") for x in hw)
    for p in range(0, len(b) - 15, 16):
        f, aux = b[p:p + 16], b[p + 15]
        st["frames"] += 1
        for i in range(8):
            x, ab = f[2 * i], aux >> i & 1
            nxt = f[2 * i + 1] if i < 7 else None
            if x & 1:
                if ab and nxt is not None:
                    data.setdefault(cur, bytearray()).append(nxt)
                cur = x >> 1
                if not ab and nxt is not None:
                    data.setdefault(cur, bytearray()).append(nxt)
            else:
                data.setdefault(cur, bytearray()).append(x & 0xFE | ab)
                if nxt is not None:
                    data.setdefault(cur, bytearray()).append(nxt)
    return data


def tpiu(samples, width=4):
    """Deframe a TPIU stream. In continuous mode TPIU inserts the half-word sync 0x7FFF (bytes FF 7F) anywhere,
    even inside a frame; the full sync 0x7FFFFFFF marks a frame start. The bit offset is the one with the most
    0x7FFF half-words; without a full sync the frame start (one of 8 positions) is the one that gives the
    longest continuous ITM counter. Returns (offset, {ID: bytes}, stats)."""
    best = None
    for off in range(16):
        b = to_bytes(samples, width, off)
        hw = [int.from_bytes(b[i:i + 2], "little") for i in range(0, len(b) - 1, 2)]
        n = hw.count(0x7FFF)
        if best is None or n > best[0]:
            best = (n, off, hw)
    n, off, hw = best
    if n == 0:
        return None, {}, {"half": 0}
    st = {"half": 0, "full": 0, "frames": 0, "phase": None}
    clean, starts = [], []
    for x in hw:
        if x == 0x7FFF:
            if clean and clean[-1] == 0xFFFF:  # 0xFFFF 0x7FFF: full sync
                clean.pop()
                st["full"] += 1
                starts.append(len(clean))
            else:
                st["half"] += 1
            continue
        clean.append(x)
    res = None
    for ph in [starts[0] % 8] if starts else range(8):
        s = dict(st, frames=0, phase=ph)
        data = frames(clean[ph:], s)
        vals, _ = itm(bytes(data.get(1, b"")))
        score = len(vals) - 10 * len(gaps_of(vals))
        if res is None or score > res[0]:
            res = (score, data, s)
    return off, res[1], res[2]


# ---- ITM ----

def itm(b):
    """ITM packets: 32-bit values written to stimulus port 0, and counts of the other packets."""
    vals, st, i = [], {"sync": 0, "overflow": 0, "other": 0}, 0
    while i < len(b):
        h = b[i]
        if h == 0:
            j = i
            while j < len(b) and b[j] == 0:
                j += 1
            if j < len(b) and b[j] == 0x80 and j - i >= 5:
                st["sync"] += 1
                i = j + 1
            else:
                i = j
            continue
        if h == 0x70:
            st["overflow"] += 1
            i += 1
            continue
        if h & 3:  # software (SWIT) or hardware source
            n = {1: 1, 2: 2, 3: 4}[h & 3]
            if i + 1 + n > len(b):
                break
            v = int.from_bytes(b[i + 1:i + 1 + n], "little")
            if not h & 4 and h >> 3 == 0 and n == 4:
                vals.append(v)
            else:
                st["other"] += 1
            i += 1 + n
            continue
        st["other"] += 1  # timestamp or extension: continuation in bit 7
        i += 1
        if h & 0x80:
            while i < len(b) and b[i] & 0x80:
                i += 1
            i += 1
    return vals, st


def itm_packets(b):
    """Whole ITM packets for listing: [(offset, bytes, description)]."""
    out, i = [], 0
    while i < len(b):
        h = b[i]
        if h == 0:
            j = i
            while j < len(b) and b[j] == 0:
                j += 1
            if j < len(b) and b[j] == 0x80:
                out.append((i, b[i:j + 1], "sync"))
                i = j + 1
            else:
                out.append((i, b[i:j], "zeros"))
                i = j
        elif h == 0x70:
            out.append((i, b[i:i + 1], "overflow"))
            i += 1
        elif h & 3:
            n = {1: 1, 2: 2, 3: 4}[h & 3]
            v = int.from_bytes(b[i + 1:i + 1 + n], "little")
            what = "hardware source" if h & 4 else f"stimulus port {h >> 3}"
            out.append((i, b[i:i + 1 + n], f"{what}: {v:#0{2 + 2 * n}x} ({v})"))
            i += 1 + n
        else:
            j = i + 1
            if h & 0x80:
                while j < len(b) and b[j] & 0x80:
                    j += 1
                j += 1
            out.append((i, b[i:j], "timestamp / extension"))
            i = j
    return out


def gaps_of(vals):
    return [(a, b) for a, b in zip(vals, vals[1:]) if (b - a) & 0xFFFFFFFF != 1]


# ---- ETM (ETMv3.5, Cortex-M4) ----

COND = "(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)?"


def etm_packets(b, alt=True):
    """ETM byte stream -> packets [{"off", "raw", "type", ...}]; alt: alternative branch address encoding
    (ETMIDR[20])."""
    out, i, last = [], 0, 0
    synced = False
    while i < len(b):
        h = b[i]
        pk = {"off": i}
        if not synced:  # skip up to the first A-sync
            if h == 0:
                j = i
                while j < len(b) and b[j] == 0:
                    j += 1
                if j < len(b) and b[j] == 0x80 and j - i >= 5:
                    synced = True
                    out.append(dict(pk, raw=b[i:j + 1], type="A-sync"))
                    i = j + 1
                    continue
            i += 1
            continue
        if h & 1:  # branch address
            j, c, val, bits = i, True, 0, 0
            exc = False
            while c and j - i < 4 and j < len(b):
                x = b[j]
                c = bool(x & 0x80)
                if j == i:
                    v, n = (x & 0x7E) >> 1, 6
                elif alt and not c:
                    exc = bool(x & 0x40)
                    v, n = x & 0x3F, 6
                else:
                    v, n = x & 0x7F, 7
                val |= v << bits
                bits += n
                j += 1
            if c and j < len(b):
                x = b[j]
                exc = bool(x & 0x40)
                val |= (x & 0xF) << bits
                bits += 4
                j += 1
            val <<= 1
            bits += 1
            e = None
            if exc and j < len(b):  # exception bytes
                e0 = b[j]
                j += 1
                e = (e0 >> 1) & 0xF
                if e0 & 0x80 and j < len(b):
                    e1 = b[j]
                    j += 1
                    if not e1 & 0x40:
                        e |= (e1 & 0x1F) << 4
                        if e1 & 0x80 and j < len(b):
                            j += 1
            mask = (1 << bits) - 1 if bits < 32 else 0xFFFFFFFF
            last = (last & ~mask) | (val & mask)
            pk.update(raw=b[i:j], type="branch", addr=last, exc=e)
            i = j
        elif h & 0x81 == 0x80:  # P-header
            if h & 3 == 0:
                atoms = "E" * ((h >> 2) & 0xF) + "N" * ((h >> 6) & 1)
            elif h & 3 == 2:
                atoms = ("N" if h & 8 else "E") + ("N" if h & 4 else "E")
            else:
                atoms = ""
            pk.update(raw=b[i:i + 1], type="P-header", atoms=atoms)
            i += 1
        elif h == 0:
            j = i
            while j < len(b) and b[j] == 0:
                j += 1
            pk.update(raw=b[i:j + 1], type="A-sync")
            i = j + 1
        elif h == 0x08:  # I-sync: info, address (4), maybe LSiP
            if i + 6 > len(b):
                break
            info = b[i + 1]
            a = int.from_bytes(b[i + 2:i + 6], "little")
            j = i + 6
            if info & 0x80:
                while j < len(b) and b[j] & 0x80:
                    j += 1
                j += 1
            last = a & ~1
            reason = ["periodic", "trace enabled", "after overflow", "debug exit"][(info >> 5) & 3]
            pk.update(raw=b[i:j], type="I-sync", addr=last, thumb=a & 1, reason=reason)
            i = j
        elif h in (0x0C, 0x66, 0x7E, 0x76):
            pk.update(raw=b[i:i + 1], type={0x0C: "trigger", 0x66: "ignore", 0x7E: "exception entry",
                                            0x76: "exception exit"}[h])
            i += 1
        elif h & 0xFB == 0x42:  # timestamp
            j = i + 1
            while j < len(b) and b[j] & 0x80:
                j += 1
            pk.update(raw=b[i:j + 1], type="timestamp")
            i = j + 1
        else:
            pk.update(raw=b[i:i + 1], type="unknown")
            synced = False  # wait for A-sync
            i += 1
        out.append(pk)
    return out


def disasm(elf, objdump=OBJDUMP):
    """{address: (size, text, kind, target, function)}; kind: None, "direct", "indirect"."""
    r = subprocess.run([objdump, "-d", elf], capture_output=True, text=True, check=True)
    out, func = {}, ""
    for line in r.stdout.splitlines():
        m = re.match(r"^([0-9a-f]+) <(.*)>:$", line)
        if m:
            func = m[2]
            continue
        m = re.match(r"^\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(\S+)\s*(.*)$", line)
        if not m:
            continue
        a = int(m[1], 16)
        size = len(m[2].replace(" ", "")) // 2
        mn, ops = m[3], m[4].split("@")[0].strip()
        kind, tgt = None, None
        if re.fullmatch(r"b(l)?" + COND + r"(\.n|\.w)?", mn) or mn in ("cbz", "cbnz"):
            t = re.search(r"\b([0-9a-f]{7,8})\b", ops)
            if t:
                kind, tgt = "direct", int(t[1], 16)
        elif mn in ("bx", "blx") or mn.startswith(("tbb", "tbh")):
            kind = "indirect"
        elif mn.startswith(("pop", "ldm")) and "pc" in ops:
            kind = "indirect"
        elif re.match(r"(ldr|mov)", mn) and ops.startswith("pc"):
            kind = "indirect"
        out[a] = (size, f"{mn}\t{ops}".strip(), kind, tgt, func)
    return out


def etm_instructions(pkts, dis):
    """Executed instructions [(address, atom, text, function, packet, atom index)] and events, with I-sync
    addresses checked against the decoder's position."""
    rows, st = [], {"isync": 0, "isync_ok": 0, "isync_bad": 0, "lost": 0, "instr": 0, "E": 0, "N": 0}
    addr = None
    for p in pkts:
        t = p["type"]
        if t == "I-sync":
            st["isync"] += 1
            if addr is not None:
                st["isync_ok" if addr == p["addr"] else "isync_bad"] += 1
                if addr != p["addr"]:
                    rows.append(("!", f"I-sync {p['addr']:#010x}, decoder expected {addr:#010x}"))
            addr = p["addr"]
            rows.append(("sync", f"I-sync {addr:#010x} ({p['reason']})"))
        elif t == "branch":
            addr = p["addr"]
            rows.append(("br", f"branch address {addr:#010x}" + (f", exception {p['exc']}" if p["exc"] else "")))
        elif t == "P-header":
            for k, a in enumerate(p["atoms"]):
                if addr is None:
                    break
                ins = dis.get(addr)
                if ins is None:
                    rows.append(("!", f"no instruction at {addr:#010x}"))
                    st["lost"] += 1
                    addr = None
                    break
                size, text, kind, tgt, func = ins
                rows.append((addr, a, text, func, p, k))
                st["instr"] += 1
                st[a] += 1
                if a == "E" and kind == "direct":
                    addr = tgt
                elif a == "E" and kind == "indirect":
                    addr = None  # wait for the branch address
                else:
                    addr += size
        elif t in ("exception entry", "exception exit"):
            rows.append(("ex", t))
    return rows, st


def func_of(dis, addr):
    x = dis.get(addr)
    return f"<{x[4]}>" if x else "<?>"


# ---- commands ----

def cmd_decode(a):
    words = load(a.file, a.words, a.skip)
    samples = remap(nibbles(words), a.lanes)
    off, data, st = tpiu(samples, 4)
    print(f"{len(words)} words = {len(samples)} nibbles")
    if off is None:
        print("no TPIU sync (FF 7F) found; first nibbles:", "".join(f"{s:x}" for s in samples[:128]))
        return 1
    ids = {str(k): len(v) for k, v in data.items()}
    vals, ist = itm(bytes(data.get(1, b"")))
    gaps = gaps_of(vals)
    print(f"TPIU: bit offset {off}, syncs: full {st['full']}, half-word {st['half']}; frame start {st['phase']}, "
          f"frames {st['frames']}, bytes per ID: {ids}")
    print(f"ITM: {len(vals)} values" + (f" from {vals[0]:#x} to {vals[-1]:#x}" if vals else "") +
          f", gaps {len(gaps)}; other packets {ist}")
    for x, y in gaps[:10]:
        print(f"  gap {x:#x} -> {y:#x}")
    ok = bool(vals) and not gaps and (a.expect is None or len(vals) == a.expect)
    if a.expect is not None and len(vals) != a.expect:
        print(f"expected {a.expect} values")
    print("PASS: ITM counter continuous" if ok else "FAIL")
    return 0 if ok else 1


def cmd_show(a):
    words = load(a.file, a.words, a.skip)
    samples = remap(nibbles(words), a.lanes)
    off, data, st = tpiu(samples, 4)
    lines = []
    w = lines.append
    w(f"{len(words)} words = {len(samples)} nibbles")
    w(f"TPIU: syncs full {st.get('full', 0)}, half-word {st['half']}, frames {st.get('frames', 0)}; bytes per ID: " +
      ", ".join(f"{k}: {len(v)}" for k, v in sorted(data.items(), key=lambda x: str(x[0]))))
    itmb, etmb = bytes(data.get(a.itm_id, b"")), bytes(data.get(a.etm_id, b""))
    vals, _ = itm(itmb)
    w(f"ITM (ID {a.itm_id}): {len(vals)} stimulus port 0 values, gaps {len(gaps_of(vals))}")
    pk = etm_packets(etmb, alt=bool(a.alt))
    dis = disasm(a.elf, a.objdump) if a.elf else {}
    rows, est = etm_instructions(pk, dis)
    kinds = {}
    for p in pk:
        kinds[p["type"]] = kinds.get(p["type"], 0) + 1
    w(f"ETM (ID {a.etm_id}): {len(etmb)} bytes, {len(pk)} packets: " + ", ".join(f"{k} {v}" for k, v in kinds.items()))
    if a.elf:
        w(f"instructions {est['instr']} (E {est['E']}, N {est['N']}); I-sync {est['isync']}, checked "
          f"{est['isync_ok'] + est['isync_bad']}: match {est['isync_ok']}, mismatch {est['isync_bad']}; "
          f"address lost {est['lost']}")
    head = list(lines)

    def section(title, items, n):
        w("")
        w(f"=== {title} ===")
        head.append("")
        head.append(f"=== {title} (first {n} of {len(items)}) ===")
        for k, x in enumerate(items):
            w(x)
            if k < n:
                head.append(x)

    section("ITM packets", [f"{o:6}  {r.hex(' '):20} {txt}" for o, r, txt in itm_packets(itmb)], a.n)

    def etm_txt(p):
        x = p["type"]
        if x == "P-header":
            x += f" {p['atoms']}"
        elif x in ("branch", "I-sync"):
            x += f" -> {p['addr']:#010x} {func_of(dis, p['addr'])}"
            if p["type"] == "I-sync":
                x += f" ({p['reason']})"
        return f"{p['off']:6}  {p['raw'].hex(' '):20} {x}"
    section("ETM packets", [etm_txt(p) for p in pk], a.n)
    if a.elf:
        section("ETM instructions", [f"  {r[0]:08x}  {r[1]}  {r[3]:12} {r[2]}" if isinstance(r[0], int)
                                     else f"  -- {r[1]}" for r in rows], a.n)
    print("\n".join(head))
    if a.out:
        open(a.out, "w").write("\n".join(lines) + "\n")
        print(f"\nfull listing: {a.out}")
    return 0


def encode(nvals=160, burst=16, idle_syncs=3, width=4):
    """Synthetic TPIU stream: ITM stimulus port 0 counter in bursts, half-word syncs in between."""
    def frames_of(data):
        out = bytearray()
        for k in range(0, len(data), 14):
            d = list(data[k:k + 14]) + [0] * (14 - len(data[k:k + 14]))  # zero padding, ITM skips it
            f = [3] + [0] * 15  # byte 0: switch to ID 1 immediately
            aux = 0
            for j, x in enumerate(d):
                pos = j + 1
                if pos % 2:
                    f[pos] = x
                else:
                    f[pos] = x & 0xFE
                    aux |= (x & 1) << (pos // 2)
            f[15] = aux
            out += bytes(f)
        return out

    stream = bytearray(SYNC * 2)
    n = 0x12345670
    while n < 0x12345670 + nvals:
        itmb = bytearray()
        for _ in range(burst):
            itmb += bytes([0x03]) + n.to_bytes(4, "little")
            n += 1
        stream += frames_of(itmb) + SYNC * idle_syncs
    bits = [(x >> b) & 1 for x in stream for b in range(8)]
    return [sum(bits[i + b] << b for b in range(width)) for i in range(0, len(bits), width)]


def cmd_encode(a):
    s = encode(nvals=a.values)
    open(a.out, "w").write("\n".join(f"{x:x}" for x in s) + "\n")
    print(f"{len(s)} nibbles, {a.values} ITM values -> {a.out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("decode", "show"):
        p = sub.add_parser(name)
        p.add_argument("file", help=".bin from usb3d.py cap or .hex (a word per line)")
        p.add_argument("--words", type=int, default=1 << 18, help="words to decode (0 = all)")
        p.add_argument("--skip", type=int, default=0, help="words to skip from the start")
        p.add_argument("--lanes", default="0123", help="lane order: bit i = TRACED<lanes[i]>")
    p = sub.choices["decode"]
    p.add_argument("--expect", type=int, help="fail unless exactly this many ITM values are decoded")
    p.set_defaults(fn=cmd_decode)
    p = sub.choices["show"]
    p.add_argument("--elf", help="target firmware, for ETM instruction reconstruction")
    p.add_argument("--objdump", default=OBJDUMP, help="arm-none-eabi-objdump (or $ARM_OBJDUMP)")
    p.add_argument("--itm-id", type=int, default=1)
    p.add_argument("--etm-id", type=int, default=2)
    p.add_argument("--alt", type=int, default=1, help="alternative branch address encoding (ETMIDR[20])")
    p.add_argument("-n", type=int, default=30, help="lines per section on screen")
    p.add_argument("-o", "--out", help="full listing")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("encode", help="synthetic TPIU stream for rtl/sim/tb_tpiu.v")
    p.add_argument("--out", default="stream.hex")
    p.add_argument("--values", type=int, default=160)
    p.set_defaults(fn=cmd_encode)
    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
