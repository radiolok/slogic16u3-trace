/* Start-up without interrupts: stack, clear .bss, main. */
    .section .text._start
    .globl _start
_start:
    csrci   mstatus, 8          /* MIE = 0 */
    la      sp, __stack_top
    la      t0, __bss_start
    la      t1, __bss_end
1:  bgeu    t0, t1, 2f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       1b
2:  call    main
3:  j       3b
