# STM32F4-Discovery (STM32F407)

ST's STM32F407 evaluation board with an on-board ST-LINK. The Cortex-M4 trace port (TPIU) is on PE2..PE6 (AF0),
the only option on this part. TRACECLK = HCLK / 2, so at 168 MHz it reaches 84 MHz.

Verified: ITM and ETM clean up to TRACECLK 57 MHz on the analyzer's stock 10 cm wires; over USB 3.0 at 50 MHz
(OSPEEDR 1, IODELAY 40) with about one corrupted nibble per 2M words where all four lines switch at once. That is
the wire harness, not the receiver: a short adapter with ground next to each signal is the fix.

- [STM32F4DISCOVERY product page](https://www.st.com/en/evaluation-tools/stm32f4discovery.html) (user manual UM1472, schematics)
- [STM32F407 documentation](https://www.st.com/en/microcontrollers-microprocessors/stm32f407-417.html): reference manual RM0090, section "Debug support" (TPIU, ITM, ETM, DBGMCU_CR)

## Wiring

| Discovery | Function | SLogic16U3 |
|---|---|---|
| PE2 | TRACECLK | CH12 |
| PE3 | TRACED0 | CH8 |
| PE4 | TRACED1 | CH9 |
| PE5 | TRACED2 | CH10 |
| PE6 | TRACED3 | CH11 |
| GND | GND | GND |

PE3 is also the accelerometer chip select on the Discovery; it does not disturb the trace.

## Bring-up

```sh
make -C tools/targets/stm32f407
openocd -f board/stm32f4discovery.cfg -c "program tools/targets/stm32f407/trace.elf verify reset exit"
python3 tools/targets/stm32f407/stm32cfg.py --src pll --pll-n 200 --hdiv 1 --ospeed 1   # TRACECLK 50 MHz
python tools/usb3d.py --dly 40 40 40 40 cap -s 1 -o run.bin
python3 tools/tpiu_decode.py decode run.bin
python3 tools/tpiu_decode.py show run.bin --elf tools/targets/stm32f407/trace.elf -o run.txt
```

At high HCLK use OSPEEDR 1 (medium) on the trace pins with loose wires: the fastest setting rings. Ringing is about
the edges, not the frequency: on a different harness layout OSPEEDR 1 and 3 broke the stream even at TRACECLK 1 MHz
(one extra TRACECLK edge every few hundred cycles), OSPEEDR 0 was clean. Check the ground wire first.

### 1- and 2-bit port

```sh
python3 tools/targets/stm32f407/stm32cfg.py --src hsi --hdiv 8 --ospeed 0 --width 2   # TRACED0..1
python3 tools/tpiu_decode.py decode run.bin --width 2
```

Verified at TRACECLK 1 MHz: 4 and 2 bits with ITM + ETM, 1 bit with ITM only (`--etm 0`). With ETM on, a 1-bit
port is busy all the time: TPIU never idles, sends no syncs, and the decoder cannot find the frames.

### SWO

The firmware's ITM also goes out of SWO (PB3, SWD mode) when the host switches TPIU over: TPI_SPPR = 2 (NRZ),
TPI_FFCR = 0x100 (formatter bypass, ITM only), TPI_ACPR = HCLK / baud - 1, DBGMCU_CR TRACE_MODE = 0. Run without
ETM (`--etm 0`). Verified with a CMSIS-DAP probe at HCLK 16 MHz: 1 and 2 Mbaud, the ITM counter continuous.

### Another debug probe

`stm32cfg.py -f <openocd.cfg>` takes any OpenOCD probe instead of the on-board ST-LINK (CN3 jumpers off), e.g. a
JTAG probe on PA13/PA14/PA15/PB3/PB4 and NRST.
