#!/usr/bin/env python3
"""Commands to the BE-U1000 tracer through a persistent OpenOCD (Tcl port 6668).

Every OpenOCD start resets BE-U1000 (the board's FTDI pulses nSRST when entering MPSSE), so the server is
started once and kept running in the background; the tracer setup survives only this way.

    ocd.py "halt; load_image $PWD/fw/trace.elf; reg pc 0x40010000; resume"
    ocd.py "pins 3; pll 1 48 6; tclk 1 4; pib_on; te_on 3 1 4; tr_status"   # BTM, TRACECLK 50 MHz
    ocd.py --stop
BAIKAL_SDK: Baikal MCU SDK directory (OpenOCD with BE-U1000 support is in Tools/OpenOCD).
BEU_USB: the board's FT2232H by USB bus location (e.g. 1-1), when several are attached.
"""
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = os.path.join(os.environ.get("BAIKAL_SDK", os.path.expanduser("~/.local/opt/baikal-mcu-sdk-2.3.0")),
                   "Tools", "OpenOCD")
PORT = 6668
LOG = os.path.join(HERE, "openocd.log")


def connect():
    try:
        return socket.create_connection(("127.0.0.1", PORT), timeout=60)
    except OSError:
        return None


def start():
    if not os.path.isdir(SDK):
        sys.exit(f"no {SDK}: set BAIKAL_SDK")
    subprocess.Popen([os.path.join(SDK, "bin", "openocd"), "-s", os.path.join(SDK, "share", "openocd", "scripts"),
                      "-f", os.path.join(HERE, "openocd.cfg"), "-f", os.path.join(HERE, "trace.tcl")],
                     stdout=open(LOG, "w"), stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(50):
        time.sleep(0.2)
        s = connect()
        if s:
            return s
    sys.exit("OpenOCD did not start:\n" + open(LOG).read()[-1500:])


def cmd(s, c):
    s.sendall(c.encode() + b"\x1a")
    buf = b""
    while not buf.endswith(b"\x1a"):
        chunk = s.recv(65536)
        if not chunk:
            sys.exit("OpenOCD closed the connection")
        buf += chunk
    return buf[:-1].decode()


def run(script):
    """Run Tcl in the server; returns puts output (it goes to the log) and the result."""
    s = connect() or start()
    pos = os.path.getsize(LOG)
    out = cmd(s, "capture {" + script + "}")
    s.close()
    with open(LOG) as f:
        f.seek(pos)
        log = "".join(x for x in f if not x.startswith(("Error: [CORE_1]", "Error: [CORE_2]")))
    return log + out


if __name__ == "__main__":
    if sys.argv[1:] == ["--stop"]:
        s = connect()
        if s:
            s.sendall(b"shutdown\x1a")
        sys.exit(0)
    print(run(" ".join(sys.argv[1:])), end="")
