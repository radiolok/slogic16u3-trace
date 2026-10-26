# rtl: the usb3d FPGA image

Gateware for the Sipeed SLogic16U3 (Gowin GW5AT-LV15MG132C1, device version B): trace port capture streamed to
the PC over USB 3.0.

```
TRACECLK, TRACED0..3 -> tpiu_rx -> afifo (TRACECLK -> pclk) -> sfifo 16 KB -> Usb3Device EP1 IN -> USB 3.0
                         IODELAY + IDDR, 32-bit words           drop counters   registers on EP0
```

| File | Contents |
|---|---|
| `usb3d.v` | top: PHY and resets, USB stack, registers, test source, PRBS mode, JTAG status and packet log; register map in the header |
| `stream.v` | stream path: async FIFO from the trace domain, 16 KB FIFO in pclk, test counter source, drop and word counters, TRACECLK frequency meter |
| `afifo.v` | async FIFO with Gray-coded pointers |
| `tpiu/tpiu_rx.v` | trace port receiver, see below |
| `usb/` | USB 3.0 device stack (Veryl) and Gowin IP generator inputs, [own README](usb/README.md) |
| `usb3d.cst`, `usb3d.sdc` | pins and clocks |
| `build.sh` | Gowin EDA build |
| `sim/` | lint and testbenches |

## tpiu: trace port receiver

`tpiu/tpiu_rx.v` is self-contained and meant to be reused outside this image: 4 data lanes through dynamic IODELAY
into IDDR clocked by TRACECLK, packed into 32-bit words in the TRACECLK domain (`o_we`, `o_wd`). Each byte is one
TRACECLK cycle, low nibble = rising edge; the first byte goes to `[7:0]`; words are aligned to the rising edge of
`run`. The format is the same for ARM TPIU and RISC-V PIB; deframing is done on the PC. It uses the Gowin GW5A
`IODELAY` and `IDDR` primitives; TRACECLK must come in on a global clock pin.

## Registers

Over EP0 vendor requests (`tools/usb3d.py` wraps them): OUT 0x03 writes 16 bits (wIndex = address), IN 0x04
reads 32 bits.

| Address | Write | Read |
|---|---|---|
| 0 | CTRL: [0] run, [2:1] source (0 trace, 1 test counter), [3] clear counters | `"RLU3"` (0x33554C52) |
| 1 | RATE: test source period, pclk cycles | [15:0] CTRL, [23:16] RATE, [31:24] version |
| 2 | IODELAY TRACED0 [7:0], TRACED1 [15:8] | TRACECLK cycles per 2^20 pclk (8.39 ms) |
| 3 | IODELAY TRACED2 [7:0], TRACED3 [15:8] | words sent to EP1 IN |
| 4 | | trace words dropped (async FIFO full) |
| 5 | | test words dropped (stream FIFO full) |
| 6 | | stream FIFO: [15:0] now, [31:16] peak |
| 7 | | IODELAY, [7:0] TRACED0 ... [31:24] TRACED3 |
| 8 | | [4:0] LTSSM state, [5] U0, [6] link configured, [7] device configured, [14:8] address |
| 9 | | trace words written to the stream FIFO |

Vendor request 0x02 = 1 replaces EP1 IN data with PRBS15 for link testing (`tools/debug/u3prbs.py`).
JTAG user registers: ER1 link status, ER2 USB packet header log (`tools/debug/u3status.py`).

## Build

```sh
sh rtl/build.sh     # -> build/usb3d.fs (SRAM over JTAG), build/usb3d.bin (flash over DFU)
```

Needs Gowin EDA 1.9.12.04 with a GW5AT license and `veryl` 0.22 in PATH. By default the script runs the Windows
install from WSL (`GOWIN=/mnt/c/Gowin/Gowin_V1.9.12.04_x64/IDE`, scratch directory on `C:`); set `GOWIN` and `WORK`
for another location. The Gowin IP (USB 3.0 PHY, SerDes wrapper, upar arbiter) is generated from the installed
EDA on the first run and cached in `WORK`.

pclk 125 MHz closes only in some placements, and Gowin PnR has no seed: the script runs `place_option` 1..4 in
parallel and keeps the variant with the fastest pclk (expect 123–125 MHz from the others). The image is built with
multiboot to `0x800000`, the factory DFU loader, so the MODE button returns the analyzer to DFU after flashing.

## Simulation and lint

```sh
sh rtl/sim/lint.sh   # Verilator -Wall on tpiu_rx, usb3d and the USB stack; any warning fails
sh rtl/sim/run.sh    # iverilog testbenches
```

| File | Checks |
|---|---|
| `sim/tb_stream.v` | stream path: continuity with random backpressure, drops equal to the counters on overflow, a word per clock |
| `sim/tb_tpiu.v` | `tpiu_rx` on a synthetic TPIU stream; the words are decoded by `tools/tpiu_decode.py`, the ITM counter must be complete and continuous |
| `sim/gowin_stubs.v` | port-only stubs of Gowin primitives for lint |
| `sim/gowin_models.v` | behavioural IODELAY and IDDR for `tb_tpiu.v` |

Tools: OSS CAD Suite (`$OSS`, default `~/.local/opt/oss-cad-suite/bin`, or PATH), Veryl, Python 3.
