#!/usr/bin/env python3
"""SLogic16U3 SPI flash access through the factory DFU loader (359f:30f1): dump, write, restore.

In DFU mode the analyzer exposes a USB-to-SPI bridge to the FPGA configuration flash (bulk OUT/IN on
interface 0). The protocol comes from Sipeed's web flasher (github.com/sipeed/slogic_ota_server, src/spi.js and
src/flash.js): packets [u32 command][u32 length][data], a block of ten u32 SPI master registers (ATCSPI200-like).

    python slogic_dfu.py id                                         # JEDEC ID, UID, size
    python slogic_dfu.py dump slogic16u3-factory.bin                # full dump: keep it, it is your way back
    python slogic_dfu.py write usb3d.bin --backup slogic16u3-factory.bin
    python slogic_dfu.py restore slogic16u3-factory.bin             # factory application back

Flash layout (16 MiB): 0x000000 application (loaded first), 0x800000 golden image = the DFU loader; GW5A falls
back to it when the image at 0x0 fails its check (UG704, 3.6.3). This tool never writes at or above 0x800000:
the golden image is the only recovery path without JTAG. `write` and `restore` first check that the golden image
in flash matches the backup, i.e. that the backup belongs to this very unit.

With --ftdi URL the same commands go straight to the flash through an FT2232H (MPSSE, pyftdi), for an opened
unit when DFU is not available.

Requires pyusb (pip install pyusb) and access to 359f:30f1 (Linux: udev rule from the web flasher README).
On Windows use Windows Python (pip install pyusb libusb-package): the loader already has WinUSB.
Enter DFU with the MODE button (the LED blinks red).
"""

import argparse
import hashlib
import struct
import sys
import time

try:
    import usb.core
    import usb.util
except ImportError:  # not needed for --ftdi
    usb = None

VID, PID_DFU = 0x359F, 0x30F1

CMD_SET_REGISTER = 0x00020000
CMD_READ_REGISTER = 0x00020001
CMD_READ_DATA = 0x00020002
REGS = 40

W_CONTROL, W_TRANSFER_FORMAT, W_TRANSFER_CONTROL = 0, 1, 2
W_TIMING, W_CONFIGURATION, W_STATUS = 4, 6, 7

MODE_WRITE_READ, MODE_WRITE_ONLY, MODE_READ_ONLY = 0x3, 0x1, 0x2
MODE_WRITE_DUMMY_READ, MODE_NO_DATA = 0x5, 0x7

READ_CHUNK = 64  # as in the web flasher for 16U3: the bridge FIFO is small
WRITE_CHUNK = 12  # the 16U3 bridge FIFO fails at 16 bytes already (devices.js)
BLOCK = 0x10000
PAGE = 0x100
GOLDEN = 0x800000


def default_regs():
    w = [0] * 10
    w[W_TRANSFER_FORMAT] = (0x2 << 16) | (0x7 << 8) | (1 << 7)
    w[W_TIMING] = (0x2 << 8) | 0xFF
    w[W_CONFIGURATION] = (0x6 << 4) | 0x6
    w[W_STATUS] = (1 << 22) | (1 << 14)
    return w


def transfer_control(mode, wr=0, rd=0, dummy=0):
    return ((mode & 0xF) << 24) | ((wr & 0x1FF) << 12) | ((dummy & 0x3) << 9) | (rd & 0x1FF)


class Bridge:
    def __init__(self, dev, timeout_ms=1000):
        self.dev = dev
        self.timeout = timeout_ms
        if sys.platform != "win32" and dev.is_kernel_driver_active(0):  # no such thing on Windows
            dev.detach_kernel_driver(0)
        dev.set_configuration()
        usb.util.claim_interface(dev, 0)
        intf = dev.get_active_configuration()[(0, 0)]
        is_bulk = lambda e: usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
        self.ep_out = next(e for e in intf if is_bulk(e) and
                           usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
        self.ep_in = next(e for e in intf if is_bulk(e) and
                          usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)

    def _send(self, cmd, data=b""):
        pkt = struct.pack("<II", cmd, len(data)) + data
        n = self.ep_out.write(pkt, self.timeout)
        if n != len(pkt):
            raise IOError(f"short write {n}/{len(pkt)}")

    def _recv(self, n):
        mps = self.ep_in.wMaxPacketSize or 64
        size = -(-n // mps) * mps
        return bytes(self.ep_in.read(size, self.timeout))[:n]

    def read_regs(self):
        self._send(CMD_READ_REGISTER)
        raw = self._recv(REGS)
        if len(raw) < REGS:
            raise IOError(f"short register read ({len(raw)} bytes)")
        return struct.unpack("<10I", raw)

    def set_regs(self, w, payload=b""):
        self._send(CMD_SET_REGISTER, struct.pack("<10I", *w) + payload)

    def assert_idle(self):
        st = self.read_regs()[W_STATUS]
        if st & 1 or not (st >> 14) & 1:
            raise IOError(f"SPI bridge busy (status 0x{st:08x})")

    def reset(self):
        w = default_regs()
        w[W_CONTROL] = 0b111
        w[W_TRANSFER_CONTROL] = transfer_control(MODE_NO_DATA)
        self.set_regs(w)

    def xfer(self, wr, rd_n=0, dummy=0):
        wr_n = len(wr)
        pad = (4 - wr_n % 4) % 4
        tx = bytes(wr) + b"\xff" * pad
        if rd_n == 0:
            tc = transfer_control(MODE_WRITE_ONLY, wr_n - 1)
        elif wr_n == 0:
            tc = transfer_control(MODE_READ_ONLY, rd=rd_n - 1)
        elif dummy <= pad:
            tc = transfer_control(MODE_WRITE_READ, wr_n - 1 + dummy, rd_n - 1)
        else:
            tc = transfer_control(MODE_WRITE_DUMMY_READ, wr_n - 1 + pad, rd_n - 1, dummy - 1 - pad)
        w = default_regs()
        w[W_TRANSFER_CONTROL] = tc
        self.set_regs(w, tx)
        data = b""
        if rd_n:
            self._send(CMD_READ_DATA)
            data = self._recv(rd_n)
        self.assert_idle()
        return data


class FtdiBridge:
    """Flash straight through an FT2232H (MPSSE, channel A): ADBUS0 SCK, ADBUS1 MOSI, ADBUS2 MISO, ADBUS3 CS#.
    For an opened unit when DFU is not available."""

    def __init__(self, url, freq=1e6):
        from pyftdi.spi import SpiController
        self.ctrl = SpiController()
        self.ctrl.configure(url)
        self.port = self.ctrl.get_port(cs=0, freq=freq, mode=0)

    def reset(self):
        pass

    def xfer(self, wr, rd_n=0, dummy=0):
        return bytes(self.port.exchange(bytes(wr) + b"\x00" * dummy, rd_n))


def open_flash(ftdi=None):
    if ftdi:
        b = FtdiBridge(ftdi)
        jid = b.xfer(b"\x9f", 3)
        if jid in (b"\xff\xff\xff", b"\x00\x00\x00"):
            sys.exit(f"flash does not respond (JEDEC ID {jid.hex()}): wiring, power, FPGA holding the bus?")
        uid = b.xfer(b"\x4b", 16, 4)
        cap = 2 ** jid[2] if 16 <= jid[2] <= 28 else 0
        return b, jid, uid, cap
    backend = None
    if sys.platform == "win32":  # Windows Python: libusb from the libusb-package wheel, WinUSB driver
        import libusb_package
        backend = libusb_package.get_libusb1_backend()
    dev = usb.core.find(idVendor=VID, idProduct=PID_DFU, backend=backend)
    if dev is None:
        sys.exit("no 359f:30f1: put the analyzer into DFU with the MODE button (LED blinks red)")
    b = Bridge(dev)
    b.reset()
    jid = b.xfer(b"\x9f", 3)
    if jid in (b"\xff\xff\xff", b"\x00\x00\x00"):
        b.reset()
        jid = b.xfer(b"\x9f", 3)
        if jid in (b"\xff\xff\xff", b"\x00\x00\x00"):
            sys.exit(f"flash does not respond (JEDEC ID {jid.hex()})")
    uid = b.xfer(b"\x4b", 16, 4)
    cap = 2 ** jid[2] if 16 <= jid[2] <= 28 else 0
    return b, jid, uid, cap


def read(b, addr, n):
    out = bytearray()
    t0 = time.time()
    while len(out) < n:
        a = addr + len(out)
        need = min(READ_CHUNK, n - len(out))
        part = b.xfer(bytes([0x0B, a >> 16 & 0xFF, a >> 8 & 0xFF, a & 0xFF]), need, 1)
        if len(part) != need:
            raise IOError(f"short read at 0x{a:06x}")
        out += part
        if len(out) % BLOCK == 0 or len(out) == n:
            rate = len(out) / max(time.time() - t0, 1e-3) / 1024
            print(f"\r  0x{len(out):06x}/0x{n:06x}  {rate:.0f} KiB/s", end="", file=sys.stderr)
    print(file=sys.stderr)
    return bytes(out)


def wait_wip(b, timeout_s=5.0):
    t0 = time.time()
    while True:
        sr = b.xfer(b"\x05", 1)[0]
        if sr == 0xFF:
            raise IOError("flash status 0xFF: SPI bus not responding")
        if not sr & 1:
            return
        if time.time() - t0 > timeout_s:
            raise IOError("flash write timed out")


def with_wren(b, op):
    b.xfer(b"\x06")
    try:
        op()
    except Exception:
        b.xfer(b"\x04")
        raise
    wait_wip(b)
    b.xfer(b"\x04")


def a24(a):
    return bytes([a >> 16 & 0xFF, a >> 8 & 0xFF, a & 0xFF])


def write_main(b, img):
    """Erase the whole application area, write img at 0x0 and verify it. Everything below 0x800000 is erased,
    not only the space img needs: otherwise a shorter image would leave the old one's tail behind."""
    if not 0 < len(img) <= GOLDEN:
        sys.exit(f"image of {len(img)} bytes does not fit 0x000000..0x{GOLDEN:06x}")
    blocks = GOLDEN // BLOCK
    for i in range(blocks):
        print(f"\r  erase {i + 1}/{blocks}", end="", file=sys.stderr)
        with_wren(b, lambda: b.xfer(b"\xd8" + a24(i * BLOCK)))
    print(file=sys.stderr)
    if read(b, 0, len(img)).count(0xFF) != len(img):
        sys.exit("flash not blank after erase")
    done, t0 = 0, time.time()
    while done < len(img):
        need = min(WRITE_CHUNK, PAGE - (done & 0xFF), len(img) - done)
        chunk = img[done:done + need]
        if chunk.count(0xFF) != need:
            with_wren(b, lambda: b.xfer(b"\x02" + a24(done) + chunk))
        done += need
        if done % 0x4000 < need or done == len(img):
            rate = done / max(time.time() - t0, 1e-3) / 1024
            print(f"\r  write 0x{done:06x}/0x{len(img):06x}  {rate:.0f} KiB/s", end="", file=sys.stderr)
    print(file=sys.stderr)
    back = read(b, 0, len(img))
    if back != img:
        diff = next(i for i in range(len(img)) if back[i] != img[i])
        sys.exit(f"verify failed: first mismatch at 0x{diff:06x}")
    print(f"written and verified {len(img)} bytes at 0x000000")


def check_golden(b, backup):
    """The golden image in flash must match the backup: the backup must come from this unit."""
    gold = backup[GOLDEN:]
    end = len(gold.rstrip(b"\xff"))
    if end == 0:
        sys.exit("no golden image at 0x800000 in the backup")
    if read(b, GOLDEN, end) != gold[:end]:
        sys.exit("golden image in flash differs from the backup: is the backup from another unit?")


def block_map(img):
    """Used 64 KiB blocks: [(start, end)]."""
    runs, start = [], None
    for off in range(0, len(img), BLOCK):
        used = img[off:off + BLOCK].count(0xFF) != min(BLOCK, len(img) - off)
        if used and start is None:
            start = off
        if not used and start is not None:
            runs.append((start, off))
            start = None
    if start is not None:
        runs.append((start, len(img)))
    return runs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("id", help="flash JEDEC ID, UID and size")
    d = sub.add_parser("dump", help="read the flash into a file")
    d.add_argument("out")
    d.add_argument("--size", type=lambda s: int(s, 0), help="bytes to read (default: whole flash)")
    w = sub.add_parser("write", help="write an image at 0x0 (the golden image is never touched)")
    w.add_argument("image")
    w.add_argument("--backup", required=True, help="full dump of this unit (dump)")
    r = sub.add_parser("restore", help="restore the factory application from a full dump")
    r.add_argument("backup")
    ap.add_argument("--ftdi", metavar="URL", help="flash straight through an FT2232H, e.g. ftdi://ftdi:2232h/1")
    a = ap.parse_args()

    if a.ftdi:  # the DFU bridge FIFO limits do not apply to FT2232H
        global READ_CHUNK, WRITE_CHUNK
        READ_CHUNK, WRITE_CHUNK = 4096, PAGE
    b, jid, uid, cap = open_flash(a.ftdi)
    print(f"JEDEC ID {jid.hex()}  UID {uid.hex()}  size {cap // 1024} KiB" if cap else
          f"JEDEC ID {jid.hex()}  UID {uid.hex()}  size unknown")
    if a.cmd == "id":
        return
    if a.cmd in ("write", "restore"):
        backup = open(a.backup, "rb").read()
        if len(backup) != cap:
            sys.exit(f"backup is {len(backup)} bytes, flash is {cap}: a full dump is needed")
        check_golden(b, backup)
        if a.cmd == "write":
            img = open(a.image, "rb").read()
        else:
            app = backup[:GOLDEN]
            img = app[:len(app.rstrip(b"\xff"))]
        write_main(b, img)
        print("now power-cycle the analyzer (replug USB)")
        return
    size = a.size or cap
    if not size:
        sys.exit("unknown flash size, use --size")
    img = read(b, 0, size)
    with open(a.out, "wb") as f:
        f.write(img)
    print(f"{a.out}: {len(img)} bytes, sha256 {hashlib.sha256(img).hexdigest()}")
    for s, e in block_map(img):
        print(f"  used 0x{s:06x}..0x{e:06x} ({(e - s) // 1024} KiB)")


if __name__ == "__main__":
    main()
