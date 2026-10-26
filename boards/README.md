# Supported boards

Targets verified with slogic16u3-trace. Any target with a 4-bit parallel trace port (ARM TPIU or RISC-V PIB,
data on both clock edges) should work the same way: TRACED0..3 → CH8..CH11, TRACECLK → CH12, GND.

| Board | Target | Trace | Verified over USB 3.0 | IODELAY |
|---|---|---|---|---|
| [STM32F4-Discovery](stm32f4-discovery.md) | STM32F407 (Cortex-M4) | TPIU: ITM + ETMv3.5 | TRACECLK 50 MHz | 40 |
| [Baikal EVU-BA-2.5](baikal-evu-ba-2.5.md) | BE-U1000 (RISC-V) | PIB: N-Trace, BTM and HTM | TRACECLK 50 MHz, 1 GB without losses | 208 |

IODELAY is the input delay of the data lines in ~12.5 ps steps (`usb3d.py --dly D D D D`). The value depends on
the target's clock-to-data timing and on the wiring, so treat the numbers as a starting point: sweep it on your
setup and take the middle of the clean window.

Demo firmware for both targets, which produces a trace the decoders can check end to end, is in
[`tools/targets/`](../tools/targets/).
