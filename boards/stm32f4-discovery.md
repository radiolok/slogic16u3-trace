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

At high HCLK use OSPEEDR 1 (medium) on the trace pins with loose wires: the fastest setting rings.
