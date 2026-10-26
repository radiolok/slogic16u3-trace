#!/usr/bin/env python3
"""Set the trace mode of the STM32F407 demo firmware (main.c) through OpenOCD and the on-board ST-LINK.

At HCLK of tens of kHz ST-LINK cannot stay connected to a running core, so the mode is written to RAM with the
core held in reset (connect_assert_srst); the firmware reads it at start.

    python3 stm32cfg.py --src pll --pll-n 200 --hdiv 1 --ospeed 1   # TRACECLK 50 MHz, ITM + ETM
    python3 stm32cfg.py --src hsi --hdiv 512                        # TRACECLK 15.6 kHz (power-on default)
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

OPENOCD = os.environ.get("OPENOCD") or shutil.which("openocd") or "openocd"
CFG_ADDR = 0x20000000
CFG_MAGIC = 0x544E4C52  # "RLNT"
SRC = {"hsi": 0, "hse": 1, "pll": 2}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", choices=SRC, default="hsi", help="SYSCLK source")
    ap.add_argument("--pll-n", type=int, default=336, help="PLL: SYSCLK = n/2 MHz, n 100..336")
    ap.add_argument("--hdiv", type=int, default=512, help="AHB divider 1..512 (not 32); TRACECLK = HCLK / 2")
    ap.add_argument("--width", type=int, default=4, choices=(1, 2, 4), help="trace port width")
    ap.add_argument("--burst", type=int, default=16, help="ITM values per burst")
    ap.add_argument("--idle", type=int, default=64, help="pause between bursts, loop passes")
    ap.add_argument("--n0", type=lambda x: int(x, 0), help="ITM counter start value (default: continue)")
    ap.add_argument("--ospeed", type=int, default=3, help="trace pin slew rate, OSPEEDR 0..3")
    ap.add_argument("--etm", type=int, default=1, help="1: ETM instruction trace (ID 2)")
    a = ap.parse_args()
    words = [CFG_MAGIC, 0, SRC[a.src], a.pll_n, a.hdiv, a.width, a.burst, a.idle]
    extra = f"write_memory {CFG_ADDR + 0x2C:#x} 32 {a.n0:#x}; " if a.n0 is not None else ""
    extra += f"write_memory {CFG_ADDR + 0x34:#x} 32 {a.ospeed}; write_memory {CFG_ADDR + 0x38:#x} 32 {a.etm}; "
    cmd = ("init; reset halt; "
           f"write_memory {CFG_ADDR:#x} 32 {{{' '.join(hex(w) for w in words)}}}; " + extra +
           f"puts \"CFG [read_memory {CFG_ADDR:#x} 32 16]\"; resume; shutdown")
    r = subprocess.run([OPENOCD, "-f", "board/stm32f4discovery.cfg",
                        "-c", "reset_config srst_only srst_nogate connect_assert_srst", "-c", cmd],
                       capture_output=True, text=True, timeout=60)
    out = r.stdout + r.stderr
    m = re.search(r"^CFG (.*)$", out, re.M)
    if not m:
        sys.exit("no answer from ST-LINK\n" + out[-1500:])
    v = [int(x, 16) for x in m[1].split()]
    print(f"previous run: hclk {v[9]} Hz, err {v[10]}, values sent {v[11]}, trace re-applied {v[12]}, "
          f"ETMIDR {v[15]:08x}")
    base = {"hsi": 16e6, "hse": 8e6, "pll": a.pll_n * 0.5e6}[a.src]
    print(f"set: {a.src} / {a.hdiv}, HCLK {base / a.hdiv / 1e3:g} kHz, TRACECLK ~{base / a.hdiv / 2e3:g} kHz, "
          f"{a.width}-bit port")


if __name__ == "__main__":
    main()
