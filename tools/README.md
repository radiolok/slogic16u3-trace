# tools: PC side

Python 3. `pip install libusb1 numpy pyusb` (Windows: also `libusb-package`). On Windows use Windows Python: the
DFU loader and the usb3d image both work through WinUSB, no driver installation and no usbipd.

| File | What it does |
|---|---|
| `slogic_dfu.py` | SLogic16U3 flash through the factory DFU loader: `id`, `dump`, `write` (checks the golden image against your backup first), `restore`; never touches the DFU loader itself |
| `usb3d.py` | usb3d host: status (`usb3d.py`), IODELAY (`--dly`), trace capture to a file (`cap`), test source with continuity check (`test`) |
| `tpiu_decode.py` | ARM TPIU decoder: formatter frames, ITM, ETMv3 packets and executed instructions from the ELF |
| `ntrace_decode.py` | RISC-V N-Trace decoder: messages, program path from the ELF (BTM and HTM) |
| `debug/u3prbs.py` | USB 3.0 throughput and integrity test (PRBS15 on EP1 IN) |
| `debug/u3status.py` | USB link state and packet header log over the FPGA JTAG (FT2232H + OpenOCD), for link debugging |
| `targets/stm32f407/` | demo trace source for STM32F4-Discovery (ITM counter + ETM) and `stm32cfg.py` to set its clock and port |
| `targets/beu1000/` | demo trace source for BE-U1000 (known branch pattern), OpenOCD config and tracer setup (`trace.tcl`, `ocd.py`) |
| `test/` | decoder unit tests: `python3 -m unittest discover tools/test` |

## Capture format

`usb3d.py cap` writes raw little-endian 32-bit words. Each byte is one TRACECLK cycle, low nibble = rising edge,
so the file read as nibbles is the trace port stream itself. Both decoders also take a `.hex` file with a word per
line (testbench output). Capturing starts after the read queue is set up and stops before it is torn down, so a
capture has no drops at its ends; `usb3d.py` prints the drop counters after every run.

## Decoders

```sh
python3 tpiu_decode.py decode run.bin                       # TPIU sync and frames, ITM port 0 counter check
python3 tpiu_decode.py show run.bin --elf fw.elf -o out.txt # ITM and ETM packets, instruction listing
python3 ntrace_decode.py decode run.bin --elf fw.elf        # N-Trace messages and program path
```

Both are plain Python (~4 s per 64K words for ETM); use `--skip` / `--words` to decode a slice of a long capture.
They need the target's objdump for `--elf`: `arm-none-eabi-objdump` / `riscv32-none-elf-objdump` in PATH, or
`ARM_OBJDUMP` / `RISCV_OBJDUMP`.
