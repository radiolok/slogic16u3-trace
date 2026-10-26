# Trace source firmware for STM32F4-Discovery. Flash with OpenOCD (on-board ST-LINK):
#   make && openocd -f board/stm32f4discovery.cfg -c "program trace.elf verify reset exit"
CROSS ?= arm-none-eabi-
CFLAGS = -mcpu=cortex-m4 -mthumb -Os -g -Wall -Wextra -ffreestanding -nostdlib

trace.elf: main.c link.ld
	$(CROSS)gcc $(CFLAGS) -T link.ld -o $@ main.c

clean:
	rm -f trace.elf
