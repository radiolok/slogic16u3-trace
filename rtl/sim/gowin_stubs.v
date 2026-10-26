// Port-only stubs of Gowin primitives and usb/board blocks, for the Verilator lint (lint.sh).
/* verilator lint_off DECLFILENAME */
/* verilator lint_off UNUSEDPARAM */
/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off UNDRIVEN */
module OSCA #(parameter FREQ_DIV = 4) (output wire OSCOUT, input wire OSCEN);
endmodule
module TLVDS_IBUF (input wire I, input wire IB, output wire O);
endmodule
module IODELAY #(parameter C_STATIC_DLY = 0, parameter DYN_DLY_EN = "FALSE", parameter ADAPT_EN = "FALSE") (
    input wire DI, input wire SDTAP, input wire VALUE, input wire [7:0] DLYSTEP, output wire DO, output wire DF);
endmodule
module IDDR (input wire D, input wire CLK, output wire Q0, output wire Q1);
endmodule
module pll_ref125 (output wire lock, output wire clkout0, input wire clkin);
endmodule
module jtag_status #(parameter integer L = 128, parameter integer L2 = 128) (
    input wire tck_pad_i, input wire tms_pad_i, input wire tdi_pad_i, output wire tdo_pad_o, input wire osc,
    input wire [L-1:0] cap, input wire [L2-1:0] cap2, output wire o_w2, output wire [30:0] o_w2_data);
endmodule
module SerDes_Top (
    output wire USB3_0_PHY_Top_pclk, output wire [31:0] USB3_0_PHY_Top_PipeRxData, output wire [3:0] USB3_0_PHY_Top_PipeRxDataK,
    output wire USB3_0_PHY_Top_PipeRxDataValid, output wire USB3_0_PHY_Top_RxElecIdle, output wire [2:0] USB3_0_PHY_Top_RxStatus,
    output wire USB3_0_PHY_Top_PhyStatus, output wire USB3_0_PHY_Top_PowerPresent, input wire USB3_0_PHY_Top_ref_clk,
    input wire USB3_0_PHY_Top_phy_resetn, input wire [31:0] USB3_0_PHY_Top_PipeTxData, input wire [3:0] USB3_0_PHY_Top_PipeTxDataK,
    input wire USB3_0_PHY_Top_TxDetectRx_loopback, input wire USB3_0_PHY_Top_TxElecIdle, input wire USB3_0_PHY_Top_RxPolarity,
    input wire USB3_0_PHY_Top_RxEqTraining, input wire USB3_0_PHY_Top_RxTermination, input wire USB3_0_PHY_Top_TxOnesZeros,
    input wire [1:0] USB3_0_PHY_Top_PowerDown, input wire USB3_0_PHY_Top_ElasticityBufferMode);
endmodule
/* verilator lint_on UNDRIVEN */
/* verilator lint_on UNUSEDSIGNAL */
/* verilator lint_on UNUSEDPARAM */
/* verilator lint_on DECLFILENAME */
