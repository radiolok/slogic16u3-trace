#!/usr/bin/env python3
"""USB 3.0 throughput and integrity test of usb3d: vendor request 0x02 = 1 replaces EP1 IN data with PRBS15
(x^15 + x^14 + 1, 8 bits per byte, bit 0 first); an async libusb transfer queue reads it and every byte is
checked. Windows (WinUSB) or Linux; pip install libusb1. Expect ~440-465 MB/s on a USB 3.0 port.

    python u3prbs.py -s 10 -q 4 -b 1048576 [-n]   # -n: no check
"""
import argparse
import time

import usb1

VID, PID = 0x1209, 0x0001


def prbs15_table():
    s, out = 0x7FFF, bytearray(32767)
    for i in range(32767):
        v = 0
        for k in range(8):
            b = ((s >> 14) ^ (s >> 13)) & 1
            s = ((s << 1) | b) & 0x7FFF
            v |= b << k
        out[i] = v
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-s", "--seconds", type=float, default=10.0)
    ap.add_argument("-q", "--queue", type=int, default=4, help="transfers in flight")
    ap.add_argument("-b", "--block", type=int, default=1 << 20, help="bytes per transfer")
    ap.add_argument("-n", "--nocheck", action="store_true")
    a = ap.parse_args()
    tab = prbs15_table()
    tab2 = tab * (a.block // len(tab) + 3)
    with usb1.USBContext() as ctx:
        h = ctx.openByVendorIDAndProductID(VID, PID)
        if h is None:
            raise SystemExit(f"no device {VID:04x}:{PID:04x}")
        h.claimInterface(0)
        h.controlWrite(0x40, 0x02, 0x0000, 0, b"")  # normal stream, then drain
        try:
            while True:
                h.bulkRead(0x81, 1 << 16, timeout=50)
        except usb1.USBErrorTimeout:
            pass
        h.controlWrite(0x40, 0x02, 0x0001, 0, b"")  # PRBS, restarts the sequence
        st = {"total": 0, "errs": 0, "phase": None, "run": True, "fail": None}
        order, done = [], {}

        def check(d):
            if st["phase"] is None:
                for skip in range(8):
                    p = (tab + tab[:64]).find(d[skip:skip + 32])
                    if p >= 0:
                        break
                if p < 0:
                    raise SystemExit(f"PRBS phase not found: {d[:16].hex()}")
                st["phase"], d = p, d[skip:]
            ref = tab2[st["phase"]:st["phase"] + len(d)]
            if d != ref:
                st["errs"] += sum(1 for x, y in zip(d, ref) if x != y)
                p = (tab + tab[:64]).find(d[-32:])
                st["phase"] = (p + 32) % len(tab) if p >= 0 else 0
            else:
                st["phase"] = (st["phase"] + len(d)) % len(tab)

        def cb(t):
            if t.getStatus() != usb1.TRANSFER_COMPLETED:
                st["fail"] = st["fail"] or t.getStatus()
                st["run"] = False
                return
            done[id(t)] = t.getBuffer()[:t.getActualLength()]
            # check strictly in submission order
            while order and id(order[0]) in done:
                t0 = order.pop(0)
                d = bytes(done.pop(id(t0)))
                st["total"] += len(d)
                if not a.nocheck:
                    check(d)
                if st["run"]:
                    t0.submit()
                    order.append(t0)

        xs = []
        for _ in range(a.queue):
            t = h.getTransfer()
            t.setBulk(0x81, a.block, callback=cb, timeout=2000)
            xs.append(t)
        t_start = time.perf_counter()
        for t in xs:
            t.submit()
            order.append(t)
        while time.perf_counter() - t_start < a.seconds and st["run"]:
            ctx.handleEventsTimeout(0.1)
        st["run"] = False
        dt = time.perf_counter() - t_start
        while any(t.isSubmitted() for t in xs):
            ctx.handleEventsTimeout(0.1)
        h.controlWrite(0x40, 0x02, 0x0000, 0, b"")
        print(f"queue {a.queue} x {a.block} B: {st['total'] / 1e6:.1f} MB in {dt:.2f} s = "
              f"{st['total'] / dt / 1e6:.1f} MB/s, bad bytes {st['errs']}"
              + ("" if a.nocheck else ", all checked") + (f", transfer failed: {st['fail']}" if st["fail"] else ""))


if __name__ == "__main__":
    main()
