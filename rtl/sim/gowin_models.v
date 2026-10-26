// Behavioural models of the Gowin primitives used by tpiu_rx, for iverilog (tb_tpiu.v). Function only:
// IODELAY delays by DLYSTEP x 12.5 ps; IDDR returns a (rising, following falling) sample pair at the next
// rising edge. Real latency of the primitives is not modelled.
`timescale 1ns/1ps
module IODELAY #(parameter C_STATIC_DLY = 0, parameter DYN_DLY_EN = "FALSE", parameter ADAPT_EN = "FALSE") (
    input wire DI, input wire SDTAP, input wire VALUE, input wire [7:0] DLYSTEP, output reg DO = 1'b0, output wire DF);
    assign DF = 1'b0;
    always @(DI) DO <= #(DLYSTEP * 0.0125) DI;
endmodule
module IDDR (input wire D, input wire CLK, output reg Q0 = 1'b0, output reg Q1 = 1'b0);
    reg r = 1'b0, f = 1'b0;
    always @(negedge CLK) f <= D;
    always @(posedge CLK) begin r <= D; Q0 <= r; Q1 <= f; end
endmodule
