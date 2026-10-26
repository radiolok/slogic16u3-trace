#!/usr/bin/env python3
"""Host side of the usb3d image: registers over EP0 vendor requests, trace stream from EP1 IN.
Windows (WinUSB binds by itself via MS OS 2.0 descriptors) or Linux; pip install libusb1 numpy.

    python usb3d.py                         # status: link, TRACECLK frequency, counters, delays
    python usb3d.py --dly 208 208 208 208   # IODELAY of TRACED0..3 (~12.5 ps steps), then status
    python usb3d.py test --rate 2 -s 10     # test source, a word every 2 pclk cycles (250 MB/s): continuity
                                            # check, gaps must match the drop counter
    python usb3d.py cap -s 5 -o run.bin     # trace to a file: little-endian 32-bit words, a byte per TRACECLK
                                            # cycle, low nibble = rising edge

Register map: rtl/usb3d.v.
"""
import argparse
import sys
import time

import numpy as np
import usb1

VID, PID = 0x1209, 0x0001
MAGIC = 0x33554C52  # "RLU3"
F_PCLK = 125e6
STATES = ["PhyWait", "Disabled", "Inactive", "RxDetReset", "RxDetActive", "RxDetQuiet", "PollLfps", "PollRxEq",
          "PollActive", "PollConfig", "PollIdle", "U0", "U3", "U3Wake", "RecActive", "RecConfig", "RecIdle",
          "HotRstActive", "HotRstExit"]


class Dev:
    def __init__(self, ctx):
        self.h = ctx.openByVendorIDAndProductID(VID, PID)
        if self.h is None:
            sys.exit(f"no device {VID:04x}:{PID:04x}")
        self.h.claimInterface(0)
        if self.rd(0) != MAGIC:
            sys.exit(f"register 0 = {self.rd(0):#010x}, not \"RLU3\": is usb3d loaded?")

    def rd(self, a):
        return int.from_bytes(self.h.controlRead(0xC0, 0x04, 0, a, 4, timeout=1000), "little")

    def wr(self, a, v):
        self.h.controlWrite(0x40, 0x03, v & 0xFFFF, a, b"", timeout=1000)

    def ctrl(self, run, src, clear=False):
        self.wr(0, (run & 1) | (src & 3) << 1 | (8 if clear else 0))

    def drain(self):
        try:
            while True:
                self.h.bulkRead(0x81, 1 << 16, timeout=50)
        except usb1.USBErrorTimeout:
            pass


def status(d):
    r = [d.rd(i) for i in range(10)]
    st = r[8] & 31
    print(f"link: {STATES[st] if st < len(STATES) else st}, U0 {r[8] >> 5 & 1}, link cfg {r[8] >> 6 & 1}, "
          f"device cfg {r[8] >> 7 & 1}, address {r[8] >> 8 & 127}; version {r[1] >> 24}, CTRL {r[1] & 0xFFFF:#x}")
    print(f"TRACECLK: {r[2] / (2 ** 20 / F_PCLK) / 1e6:.3f} MHz; IODELAY {[r[7] >> 8 * i & 255 for i in range(4)]}")
    print(f"words: sent {r[3]}, trace in {r[9]}, dropped: trace {r[4]}, test {r[5]}; "
          f"sfifo now {r[6] & 0xFFFF}, peak {r[6] >> 16}")


def stream(ctx, d, seconds, block, queue, sink, start=None, stop=None):
    """Async EP1 IN read queue; sink(bytes) gets data in order. start() runs once the reads are queued (turn the
    source on only when reads are waiting, otherwise the FIFO overflows before the first one); stop() runs at the
    end, before the reads stop, so the rest of the FIFO is still read out. Returns (bytes, seconds)."""
    st = {"n": 0, "run": True, "fail": None}
    order, done = [], {}

    def cb(t):
        if t.getStatus() not in (usb1.TRANSFER_COMPLETED, usb1.TRANSFER_TIMED_OUT):
            if t.getStatus() != usb1.TRANSFER_CANCELLED:
                st["fail"] = st["fail"] or t.getStatus()
            st["run"] = False
            return
        done[id(t)] = bytes(t.getBuffer()[:t.getActualLength()])
        while order and id(order[0]) in done:
            t0 = order.pop(0)
            b = done.pop(id(t0))
            st["n"] += len(b)
            sink(b)
            if st["run"]:
                t0.submit()
                order.append(t0)

    xs = []
    for _ in range(queue):
        t = d.h.getTransfer()
        t.setBulk(0x81, block, callback=cb, timeout=200)
        xs.append(t)
    t0 = time.perf_counter()
    for t in xs:
        t.submit()
        order.append(t)
    if start:
        start()
    while time.perf_counter() - t0 < seconds and st["run"]:
        ctx.handleEventsTimeout(0.1)
    dt = time.perf_counter() - t0
    if stop and st["run"]:
        stop()
        t1 = time.perf_counter()
        while time.perf_counter() - t1 < 0.5 and st["run"]:
            ctx.handleEventsTimeout(0.1)
    st["run"] = False
    while any(t.isSubmitted() for t in xs):
        ctx.handleEventsTimeout(0.1)
    if st["fail"]:
        print(f"transfer failed: {st['fail']}")
    return st["n"], dt


def cmd_test(ctx, d, a):
    d.ctrl(0, 1)
    d.drain()
    d.wr(1, a.rate)
    d.ctrl(0, 1, clear=True)
    s = {"exp": None, "gaps": 0, "lost": 0, "back": 0, "tail": b""}

    def sink(b):
        b = s["tail"] + b
        n = len(b) // 4
        s["tail"] = b[4 * n:]
        if not n:
            return
        w = np.frombuffer(b, "<u4", n).astype(np.int64)
        prev = np.empty_like(w)
        prev[0] = w[0] if s["exp"] is None else s["exp"]
        prev[1:] = w[:-1] + 1
        diff = w - prev
        s["gaps"] += int(np.count_nonzero(diff))
        s["lost"] += int(diff[diff > 0].sum())
        s["back"] += int(np.count_nonzero(diff < 0))
        s["exp"] = int(w[-1]) + 1

    n, dt = stream(ctx, d, a.seconds, a.block, a.queue, sink, start=lambda: d.ctrl(1, 1),
                   stop=lambda: d.ctrl(0, 1))
    time.sleep(0.05)
    nw, gdrop = d.rd(3), d.rd(5)
    print(f"test rate {a.rate} ({F_PCLK / max(a.rate, 1) * 4 / 1e6:.0f} MB/s from the source): received "
          f"{n / 1e6:.1f} MB in {dt:.2f} s = {n / dt / 1e6:.1f} MB/s")
    print(f"  gaps {s['gaps']}, missing by gaps {s['lost']}, backwards {s['back']}; drop counter {gdrop}; "
          f"sent {nw} words, received {n // 4}")
    ok = s["back"] == 0 and s["lost"] <= gdrop and (gdrop == 0) == (s["lost"] == 0)
    print("  " + ("consistent" if ok else "NOT consistent") + (f" (the last {gdrop - s['lost']} drops came after the "
                                                           f"last received word)" if gdrop > s["lost"] else ""))
    status(d)


def cmd_cap(ctx, d, a):
    d.ctrl(0, 0)
    d.drain()
    d.ctrl(0, 0, clear=True)
    f = open(a.out, "wb")
    n, dt = stream(ctx, d, a.seconds, a.block, a.queue, f.write, start=lambda: d.ctrl(1, 0),
                   stop=lambda: d.ctrl(0, 0))
    f.close()
    print(f"trace: {n} bytes in {dt:.2f} s = {n / dt / 1e6:.2f} MB/s -> {a.out}")
    status(d)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dly", type=int, nargs=4, metavar="D", help="IODELAY TRACED0..3")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("test", "cap"):
        p = sub.add_parser(name)
        p.add_argument("-s", "--seconds", type=float, default=5.0)
        p.add_argument("-b", "--block", type=int, default=1 << 20)
        p.add_argument("-q", "--queue", type=int, default=4)
        if name == "test":
            p.add_argument("--rate", type=int, default=4, help="source period, pclk cycles (1 = 500 MB/s)")
        else:
            p.add_argument("-o", "--out", default="trace.bin")
    a = ap.parse_args()
    with usb1.USBContext() as ctx:
        d = Dev(ctx)
        if a.dly:
            d.wr(2, a.dly[0] | a.dly[1] << 8)
            d.wr(3, a.dly[2] | a.dly[3] << 8)
        if a.cmd == "test":
            cmd_test(ctx, d, a)
        elif a.cmd == "cap":
            cmd_cap(ctx, d, a)
        else:
            status(d)


if __name__ == "__main__":
    main()
