# BE-U1000 tracer over SBA: clock, pins, PIB, encoder. Sourced after openocd.cfg.
# Addresses and fields: BE-U1000 Reference Manual v1.3.0, ch. 5 (CRU) and 16 (Tracer).

set CRU_CLKSEL  0x14000000
set CRU_PLLSET  0x14000004
set CRU_IOAFCR2 0x14000044
set CRU_IODSCR1 0x14000058
set CRU_CLKCR1  0x14000084
set TR_TE       0x15104000
set TR_RAM      0x15105000
set TR_PIB      0x15106000
set TRACE_SEL   0x4000A014

proc rd {a} { return [read_memory $a 32 1] }
proc wr {a v} { write_memory $a 32 $v }
proc rmw {a clr set} { wr $a [expr {([rd $a] & ~$clr) | $set}] }

# Wait until a field reads back as written (tr??Active "may take an arbitrary time").
proc wait_bits {a mask val} {
	for {set i 0} {$i < 100} {incr i} {
		if {([rd $a] & $mask) == $val} { return }
		sleep 1
	}
	error [format "register %#x: expected %#x under mask %#x, reads %#x" $a $val $mask [rd $a]]
}

# PLL from CLKI 25 MHz: Fout = 25 * nf / (nr * od), VCO = 25 * nf / nr. Like QSPI_clk_init in the SDK, but
# core and peripheral clocks are left alone: the PLL only feeds the tclk divider.
proc pll {nr nf od} {
	global CRU_CLKSEL CRU_PLLSET
	rmw $CRU_CLKSEL 0x1 0x0                ;# REFCLKSEL = C0 (CLKI)
	rmw $CRU_PLLSET 0x400000 0             ;# BYPASS = 0
	rmw $CRU_PLLSET 0 0x1000000            ;# RESET = 1
	rmw $CRU_PLLSET 0xFFFFF [expr {0x200000 | ($nf - 1) << 14 | ($od - 1) << 10 | ($nf - 1) << 4 | ($nr - 1)}]
	rmw $CRU_PLLSET 0x1000000 0            ;# RESET = 0
	sleep 1
}

# Cores on PLL / cdiv, bus and peripherals (hclk, pclk0..2) on PLL / pdiv, 1 MHz timer clocks to match.
# Like QSPI_clk_init in the SDK, without restarting the PLL (set up by proc pll).
proc core {cdiv pdiv} {
	global CRU_CLKDIV_DIV1 CRU_CLK_SRC_PLL
	set cc [expr {$cdiv == 1 ? 0 : ($cdiv - 1) << 1}]
	set pc [expr {$pdiv == 1 ? 0 : ($pdiv - 1) << 1}]
	foreach p {PCLK0 PCLK1 PCLK2 HCLK} {
		___CRU_Set${p}Prescaler $pc; ___CRU_${p}Prescaler_Enable; ___CRU_Set${p}Source 1
	}
	___CRU_SetCCLKPrescaler $cc; ___CRU_CCLKPrescaler_Enable; ___CRU_SetCCLKSource 1
	set f [expr {200 / $cdiv}]
	___CRU_SetCCLK1MHzPrescaler [expr {$f - 1}]
	___CRU_SetHCLK1MHzPrescaler [expr {200 / $pdiv - 1}]
}

# tclk: tracer clock (max 100 MHz). src: 0 sys_clk, 1 PLL / div, 2 alt_clk.
# div: integer 1..16 or n.5 (1.5..15.5), as the TCLKDIV field.
proc tclk {src {div 1}} {
	global CRU_CLKSEL CRU_CLKCR1
	if {$div == 1} {
		set code 0
	} elseif {$div == int($div)} {
		set code [expr {(int($div) - 1) << 1}]
	} else {
		set code [expr {(int($div - 1.5) << 1) | 1}]
	}
	rmw $CRU_CLKCR1 0x3F [expr {0x20 | $code}]
	rmw $CRU_CLKSEL 0x1800 [expr {$src << 11}]
}

# PB0..PB3 = PIB_D[0..3], PB4 = PIB_CLK (AF4); ds: drive strength 0..3 (4.5..18 mA).
proc pins {{ds 0}} {
	global CRU_IOAFCR2 CRU_IODSCR1
	rmw $CRU_IOAFCR2 0xFFFFF 0xCCCCC
	set m 0; set v 0
	for {set i 0} {$i < 5} {incr i} {
		set m [expr {$m | 3 << 2 * $i}]
		set v [expr {$v | $ds << 2 * $i}]
	}
	rmw $CRU_IODSCR1 $m $v
}

# Order per the Trace Control Interface: reset -> Active -> Enable (sinks before the encoder).
proc pib_on {} {
	global TR_PIB
	wr $TR_PIB 0; wait_bits $TR_PIB 1 0
	wr $TR_PIB 1; wait_bits $TR_PIB 1 1
	wr $TR_PIB 3; wait_bits $TR_PIB 2 2
}

# mode: 3 branch trace (BTM), 6 branch history (HTM), as in the spec; RM v1.3.0 says 3/2, but 2 does not stick
# (WARL: 0..2 -> 0, 3..5 -> 3, 6..7 -> 6).
# sync: 0 off, 1 by messages, 2 by core clocks, 3 by half-words; period 2^(syncmax+4).
# trig = 1: trTeInstTracing is switched by debug triggers (trig_set) instead of being set here.
proc te_on {{mode 3} {sync 1} {syncmax 4} {stall 0} {trig 0}} {
	global TR_TE
	wr $TR_TE 0; wait_bits $TR_TE 1 0
	wr $TR_TE 1; wait_bits $TR_TE 1 1
	set c [expr {1 | $mode << 4 | $trig << 11 | $stall << 13 | $sync << 16 | $syncmax << 20}]
	wr $TR_TE $c
	wr $TR_TE [expr {$c | 2}]; wait_bits $TR_TE 2 2
	if {!$trig} { wr $TR_TE [expr {$c | 6}] }
}

# Core 0 trigger (Debug Spec, mcontrol) on executing addr in M-mode, action act:
# 2 trace-on, 3 trace-off, 4 trace-notify. The core is halted while the CSRs are written.
proc trig_set {idx addr act} {
	set run [expr {[CORE_0 curstate] eq "running"}]
	if {$run} { halt }
	reg tselect $idx
	reg tdata1 0
	reg tdata2 $addr
	reg tdata1 [format 0x%x [expr {2 << 28 | $act << 12 | 1 << 6 | 1 << 2}]]
	puts "trigger $idx: tdata1 [lindex [reg tdata1] end] tdata2 [lindex [reg tdata2] end]"
	if {$run} { resume }
}

proc trig_clear {} {
	set run [expr {[CORE_0 curstate] eq "running"}]
	if {$run} { halt }
	for {set i 0} {$i < 4} {incr i} { reg tselect $i; reg tdata1 0 }
	if {$run} { resume }
}

proc te_off {} {
	global TR_TE
	rmw $TR_TE 0x6 0
	wait_bits $TR_TE 0xA 0x8
}

# Discovery per the Trace Control Interface: write all ones to each word, read back, restore. Prints only words
# that read non-zero or hold bits. The encoder is Active, not Enabled, meanwhile. base: $TR_TE, $TR_PIB, $TR_RAM;
# from/to: offsets.
proc discover {base {from 0} {to 0xFFC}} {
	set ctl [rd $base]
	wr $base 1
	for {set off $from} {$off <= $to} {incr off 4} {
		set a [expr {$base + $off}]
		set old [rd $a]
		if {$off != 0} { wr $a 0xFFFFFFFF }
		set ones [rd $a]
		if {$off != 0} { wr $a $old }
		if {$old != 0 || $ones != 0} {
			puts [format "  +%#05x: was %#010x, after writing ones %#010x" $off $old $ones]
		}
	}
	wr $base $ctl
}

proc tr_status {} {
	global CRU_CLKSEL CRU_PLLSET CRU_CLKCR1 CRU_IOAFCR2 CRU_IODSCR1 TR_TE TR_PIB TR_RAM
	puts [format "CLKSEL %#x PLLSET %#x CLKCR1 %#x IOAFCR2 %#x IODSCR1 %#x" \
		[rd $CRU_CLKSEL] [rd $CRU_PLLSET] [rd $CRU_CLKCR1] [rd $CRU_IOAFCR2] [rd $CRU_IODSCR1]]
	puts [format "TE %#x IMPL %#x FEAT %#x  PIB %#x  RAM %#x" \
		[rd $TR_TE] [rd [expr {$TR_TE + 4}]] [rd [expr {$TR_TE + 8}]] [rd $TR_PIB] [rd $TR_RAM]]
}
return
