#!/usr/bin/env python3
"""usb3d link status over the FPGA JTAG (GW_JTAG user registers), for USB 3.0 bring-up and debugging.
ER1: LTSSM state, state history, counters; ER2: log of USB packet headers. Register layout and header decoding
follow ciniml's host/usb3_status.py and usb3_trace.py (BSL-1.0).

Needs an FT2232H on the SLogic16U3 JTAG connector (channel A) and OpenOCD (OSS CAD Suite or $OPENOCD).

    python3 u3status.py            # one snapshot
    python3 u3status.py -w 1       # a snapshot every second
    python3 u3status.py log        # header log: 128 entries since the first U0
    python3 u3status.py rearm 3    # restart the log; mode 3 = ring of EP1 IN packets until a Usb3Device event
                                   # (ACK without reply, retry, loss) plus 64 after
FPGA_USB=1-2 selects the adapter by USB bus location when several FT2232H are attached.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

OPENOCD = os.environ.get("OPENOCD") or shutil.which("openocd") or \
    os.path.expanduser("~/.local/opt/oss-cad-suite/bin/openocd")
CFG = """adapter driver ftdi
ftdi vid_pid 0x0403 0x6010
ftdi channel 0
ftdi layout_init 0x0008 0x000b
adapter speed 2000
transport select jtag
jtag newtap gw5a tap -irlen 8 -expected-id 0x0001681b
"""
if os.environ.get("FPGA_USB"):
    CFG += f"adapter usb location {os.environ['FPGA_USB']}\n"


def openocd(cmds, timeout=120):
    r = subprocess.run([OPENOCD, "-c", CFG.replace("\n", "; "), "-c", "init; " + "; ".join(cmds) + "; shutdown"],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout + r.stderr


OSC_HZ = 51.66e6  # image oscillator (pclk counter and uptime use it, ~1 %)
MAGIC = 0x5B31
NBITS = 448       # 432 ER1 bits + slack (an extra bit comes first; the frame is found by its tag)
STATES = ["PhyWait", "Disabled", "Inactive", "RxDetReset", "RxDetActive", "RxDetQuiet", "PollLfps", "PollRxEq",
          "PollActive", "PollConfig", "PollIdle", "U0", "U3", "U3Wake", "RecActive", "RecConfig", "RecIdle",
          "HotRstActive", "HotRstExit"]


def st(n):
    return STATES[n] if n < len(STATES) else f"?{n}"


def read():
    out = openocd(["irscan gw5a.tap 0x42", f'puts "DR [drscan gw5a.tap {NBITS} 0]"'])
    m = re.search(r"^DR (\S+)$", out, re.M)
    if not m:
        sys.exit("no data from OpenOCD:\n" + out[-1500:])
    raw = int(m[1], 16)
    for off in range(8):
        v = raw >> off
        if v & 0xFFFF == MAGIC:
            break
    else:
        sys.exit(f"no tag 0x{MAGIC:04x} in ER1: is usb3d loaded?")

    def f(lo, w):
        return (v >> lo) & ((1 << w) - 1)
    return {
        "ref_lock": f(16, 1), "phy_resetn": f(17, 1), "pclk": f(24, 20) * 16 * OSC_HZ / (1 << 22),
        "prst": f(44, 1), "in_u0": f(45, 1), "link_cfg": f(46, 1), "dev_cfg": f(47, 1),
        "tx_detrx": f(51, 1), "tx_eidle": f(52, 1), "pd": f(53, 2), "state": f(56, 5), "st_max": f(64, 5),
        "rx_status": f(72, 3), "rx_eidle": f(76, 1), "power_present": f(77, 1),
        "addr": f(80, 7), "trans": f(88, 8), "u0": f(112, 16), "hp": f(128, 16), "resets": f(144, 16),
        "dpp_err": f(160, 16), "physt": f(176, 16), "lfps": f(192, 16), "seen": f(208, 32),
        "trace": [f(240 + 5 * i, 5) for i in range(16)], "up": f(352, 32) * 65536 / OSC_HZ,
        "com": f(384, 16), "dec_err": f(400, 16), "disp_err": f(416, 16),
    }


def show(s):
    print(f"uptime {s['up']:.1f} s, ref_lock {s['ref_lock']}, phy_resetn {s['phy_resetn']}, pclk {s['pclk'] / 1e6:.2f} MHz, "
          f"power_present {s['power_present']}, PhyStatus {s['physt']}")
    print("history:", " > ".join(st(x) for x in reversed(s["trace"])))
    print(f"{st(s['state'])} (furthest: {st(s['st_max'])}): U0 {s['in_u0']}, link_cfg {s['link_cfg']}, "
          f"dev_cfg {s['dev_cfg']}, address {s['addr']}, P{s['pd']}, TxElecIdle {s['tx_eidle']}, "
          f"TxDetectRx {s['tx_detrx']}, RxElecIdle {s['rx_eidle']}, RxStatus {s['rx_status']}")
    print(f"U0 entries {s['u0']}, headers {s['hp']}, resets {s['resets']}, DPP errors {s['dpp_err']}, "
          f"LFPS {s['lfps']}, transitions {s['trans']}; rx: COM {s['com']}, 8b10b errors {s['dec_err']}, "
          f"disparity errors {s['disp_err']}")


L2 = 192          # ER2: bit 191 = 1 writes, [190:160] = command word
MAGIC2 = 0x5B34
TP_SUB = {1: "ACK", 2: "NRDY", 3: "ERDY", 4: "STATUS", 5: "STALL", 6: "DEV_NOTIFY", 7: "PING", 8: "PING_RSP"}
LMP_SUB = {1: "SetLinkFunction", 2: "U2InactivityTimeout", 3: "VendorTest", 4: "PortCapability",
           5: "PortConfiguration", 6: "PortConfigResponse"}


def decode_hdr(h):
    dw0, dw1, dw2 = h & 0xFFFFFFFF, (h >> 32) & 0xFFFFFFFF, (h >> 64) & 0xFFFFFFFF
    typ, addr, route = dw0 & 0x1F, dw0 >> 25, (dw0 >> 5) & 0xFFFFF
    if typ == 0x00:
        sub = (dw0 >> 5) & 0xF
        return f"LMP {LMP_SUB.get(sub, sub)} dw0={dw0:08x} dw1={dw1:08x} dw2={dw2:08x}"
    if typ == 0x04:
        return (f"TP  {TP_SUB.get(dw1 & 0xF, dw1 & 0xF):8s} addr={addr} route={route:x} ept={(dw1 >> 8) & 0xF} "
                f"dir={(dw1 >> 7) & 1} rty={(dw1 >> 6) & 1} nump={(dw1 >> 16) & 0x1F} seq={(dw1 >> 21) & 0x1F}")
    if typ == 0x08:
        return (f"DP  addr={addr} route={route:x} seq={dw1 & 0x1F} eob={(dw1 >> 6) & 1} dir={(dw1 >> 7) & 1} "
                f"ept={(dw1 >> 8) & 0xF} setup={(dw1 >> 15) & 1} len={dw1 >> 16}")
    if typ == 0x1F:  # ring mode: Usb3Device EP1 IN state
        st = dw2 << 32 | dw1
        return (f"    EP1 IN: seq_in={st & 0x1F} limit={(st >> 5) & 0x1F} nsent={(st >> 10) & 0x1F} "
                f"ack={(st >> 15) & 0xF} ready={(st >> 19) & 0xFFFF:016b} stop={(st >> 36) & 1} flow={(st >> 37) & 1}")
    return f"type {typ:02x} {dw0:08x} {dw1:08x} {dw2:08x}"


def er2_scans(words):
    """ER2 scans writing command words; each scan returns the snapshot taken before its own write."""
    cmds = []
    for w in words:
        cmds += ["irscan gw5a.tap 0x43", f'puts "R [drscan gw5a.tap {L2} {(1 << (L2 - 1)) | (w << (L2 - 32)):#x}]"']
    out = openocd(cmds)
    res = []
    for r in re.findall(r"^R (\S+)$", out, re.M):
        raw = int(r, 16)
        for off in range(8):
            if (raw >> off) & 0xFFFF == MAGIC2:
                res.append(raw >> off)
                break
        else:
            sys.exit(f"no tag 0x{MAGIC2:04x} in ER2: is usb3d loaded?")
    return res


def cmd_log():
    snaps = er2_scans(list(range(128)) + [0])[1:]   # snapshot k is the entry selected by scan k - 1
    v = snaps[-1]
    hwp, mode, wrap, frz = (v >> 144) & 0xFF, (v >> 152) & 3, (v >> 154) & 1, (v >> 155) & 1
    n = 128 if hwp & 0x80 else hwp & 0x7F
    order = [(hwp + i) & 0x7F for i in range(128)] if wrap else list(range(n))
    print(f"log: mode {mode}, entries {n if not wrap else 128}{', ring wrapped' if wrap else ''}"
          f"{', stopped' if frz else ''}")
    t0 = None
    for i in order:
        s = snaps[i]
        if (s >> 16) & 0x7F != i:
            print(f"  (entry {(s >> 16) & 0x7F} instead of {i})")
        e = (s >> 23) & ((1 << 121) - 1)
        tx, ts, h = e >> 120, (e >> 96) & 0xFFFFFF, e & ((1 << 96) - 1)
        t0 = ts if t0 is None else t0
        print(f"{i:3d} {((ts - t0) & 0xFFFFFF) * 8e-3:10.3f} us {'->' if tx else '<-'} {decode_hdr(h)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-w", type=float, metavar="SEC", help="repeat with this period")
    ap.add_argument("cmd", nargs="?", choices=("log", "rearm"))
    ap.add_argument("mode", nargs="?", type=int, default=1, help="rearm: log mode (3 = EP1 IN ring)")
    a = ap.parse_args()
    if a.cmd == "log":
        return cmd_log()
    if a.cmd == "rearm":
        er2_scans([1 << 30 | (a.mode & 3) << 28])
        return print(f"log restarted, mode {a.mode & 3}")
    while True:
        show(read())
        if not a.w:
            break
        time.sleep(a.w)
        print()


if __name__ == "__main__":
    main()
