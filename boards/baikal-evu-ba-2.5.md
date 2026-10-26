# Baikal EVU-BA-2.5 (BE-U1000)

Evaluation board for the Baikal Electronics BE-U1000 RISC-V microcontroller. Its trace encoder speaks N-Trace
(RISC-V Nexus trace) over PIB: 4 data lines and a clock, data on both edges. Branch trace (BTM) and branch history
(HTM) both decode; the tracer is set up over the system bus with OpenOCD from the Baikal MCU SDK while the core
keeps running.

Verified over USB 3.0: TRACECLK 50 MHz, 20 s = 1 GB of trace, no losses, the decoder follows the program path
through the ELF without a single error.

- [Baikal MCU SDK](https://gitflic.ru/project/baikalelectronics/mcu-sdk): toolchain, OpenOCD with BE-U1000 support
- [RISC-V N-Trace specification](https://github.com/riscv-non-isa/riscv-nexus-trace)

## Wiring

PIB on PB0..PB4 (AF4), all on connector XP10.

| XP10 pin | MCU pin | Function | SLogic16U3 |
|---|---|---|---|
| 1 | PB0 | PIB_D[0] | CH8 |
| 2 | PB1 | PIB_D[1] | CH9 |
| 3 | PB2 | PIB_D[2] | CH10 |
| 19 | PB3 | PIB_D[3] | CH11 |
| 6 | PB4 | PIB_CLK | CH12 |
| 4, 20 | | GND | GND |

**Remove R5 (22 Ω) before connecting.** On this board the QSPI flash DD1 sits on PB0..PB5: PB0 is its chip select
through R5. With R5 in place a random trace stream eventually selects the flash, which then drives DQ1 against
PIB_D[3], and a random write-enable + erase sequence can wipe it. Without R5 the flash stays deselected (R4 pulls
S# up). Cost: no QSPI boot until R5 is put back.

Board setup: boot "JTAG EXT" (XP1 DBG-M2-M1-M0 = 0100); clock from the 25 MHz oscillator (CLK jumper on XP1
removed), since the internal RC oscillator drifts.

## Bring-up

Every OpenOCD start resets BE-U1000, so `ocd.py` keeps one OpenOCD server running and sends it Tcl commands.

```sh
export BAIKAL_SDK=~/baikal-mcu-sdk-2.3.0 PATH=$BAIKAL_SDK/Tools/toolchain/riscv32-none-elf/bin:$PATH
make -C tools/targets/beu1000/fw
O=tools/targets/beu1000/ocd.py
python3 $O "halt; load_image $PWD/tools/targets/beu1000/fw/trace.elf; reg pc 0x40010000; resume"
python3 $O "pins 3; pll 1 48 6; tclk 1 4; pib_on; te_on 3 1 4"   # BTM, TRACECLK 50 MHz (HTM: te_on 6 1 4)
python tools/usb3d.py --dly 208 208 208 208 cap -s 5 -o run.bin
python3 tools/ntrace_decode.py decode run.bin --elf tools/targets/beu1000/fw/trace.elf
```

The trace clock runs only while the encoder or PIB calibration is active.
