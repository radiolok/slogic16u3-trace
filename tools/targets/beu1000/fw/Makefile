# Trace source firmware for BE-U1000 (core 0, runs from TCM). Toolchain: Baikal MCU SDK (riscv32-none-elf-*) in
# PATH, or CROSS=<prefix>. Load with ocd.py: "halt; load_image <path>/trace.elf; reg pc 0x40010000; resume".
CROSS ?= riscv32-none-elf-
CFLAGS = -march=rv32imac_zicsr -mabi=ilp32 -O2 -g -ffreestanding -nostdlib -Wall

trace.elf: start.S main.c link.ld
	$(CROSS)gcc $(CFLAGS) -T link.ld -o $@ start.S main.c
	$(CROSS)size $@

clean:
	rm -f trace.elf
