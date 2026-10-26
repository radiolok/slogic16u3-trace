/* BE-U1000, core 0: code in TCMA, data and stack in TCMB. Loaded with OpenOCD (load_image). */
MEMORY {
    TCMA (rx)  : ORIGIN = 0x40010000, LENGTH = 64K
    TCMB (rw)  : ORIGIN = 0x40020000, LENGTH = 96K
}
ENTRY(_start)
SECTIONS {
    .text : { KEEP(*(.text._start)) *(.text*) *(.rodata*) } > TCMA
    .data : { *(.sdata*) *(.data*) } > TCMB
    .bss (NOLOAD) : { __bss_start = .; *(.sbss*) *(.bss*) *(COMMON) __bss_end = .; } > TCMB
    __stack_top = ORIGIN(TCMB) + LENGTH(TCMB);
}
