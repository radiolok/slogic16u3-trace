// usb3d clocks: the reference's (ciniml board/timing.sdc) plus TRACECLK, constrained at 100 MHz (STM32F407 tops out
// at 84 MHz). Domains meet only through afifo, Gray-coded counters and quasi-static settings (IODELAY, run).
create_clock -name pclk -period 8 -waveform {0 4} [get_nets {pclk}]
create_clock -name ref_clk -period 8 -waveform {0 4} [get_nets {ref_clk}]
create_clock -name rx_clk -period 8 -waveform {0 4} [get_nets {u_serdes/q0_lane0_fabric_rx_clk}]
create_clock -name drp_clk -period 10 -waveform {0 5} [get_nets {u_serdes/upar_arbiter_wrap_SerDes_Top_inst_drp_clk_o[0]}]
create_clock -name osc -period 19.0 -waveform {0 9.5} [get_nets {osc}]
create_clock -name tck -period 100 -waveform {0 50} [get_ports {tck_pad_i}]
create_clock -name traceclk -period 10 -waveform {0 5} [get_ports {ch[12]}]
set_clock_groups -asynchronous -group [get_clocks {pclk}] -group [get_clocks {rx_clk}] -group [get_clocks {drp_clk}] -group [get_clocks {ref_clk}] -group [get_clocks {osc}] -group [get_clocks {tck}] -group [get_clocks {traceclk}]
