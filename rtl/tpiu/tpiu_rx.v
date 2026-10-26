// Parallel trace port receiver: 4 data lanes, DDR, clocked by the target's TRACECLK.
// Works for ARM TPIU (ITM/ETM) and RISC-V PIB (N-Trace) alike: both send one nibble per clock edge.
//
//   TRACEDn -> IODELAY (dynamic, ~12.5 ps per step) -> IDDR (TRACECLK) -> 32-bit word
//
// One byte per TRACECLK cycle: {nibble at the falling edge, nibble at the rising edge}, i.e. the earlier nibble
// in the low half. Four bytes make a word, the first byte in [7:0]. Words are aligned to the rising edge of run.
// Gowin GW5A primitives (IODELAY, IDDR); tclk must come from a global clock pin.
module tpiu_rx (
    input  wire        tclk,          // TRACECLK
    input  wire [3:0]  traced,        // TRACED0..3 pads
    input  wire [31:0] dly,           // IODELAY steps: [7:0] TRACED0 ... [31:24] TRACED3; quasi-static, no sync
    input  wire        run,           // any clock domain, synchronised here
    output reg         o_we = 1'b0,   // o_wd is a new word (tclk domain)
    output reg  [31:0] o_wd = 32'd0
);
    wire [3:0] dd, df, q0, q1;
    genvar g;
    generate
        for (g = 0; g < 4; g = g + 1) begin : ddr
            IODELAY #(.C_STATIC_DLY(0), .DYN_DLY_EN("TRUE"), .ADAPT_EN("FALSE")) dl (
                .DI(traced[g]), .SDTAP(1'b0), .VALUE(1'b0), .DLYSTEP(dly[8 * g +: 8]),
                .DO(dd[g]), .DF(df[g]));
            IDDR id (.D(dd[g]), .CLK(tclk), .Q0(q0[g]), .Q1(q1[g]));
        end
    endgenerate

    reg [2:0]  run_s = 3'd0;
    reg [1:0]  bcnt = 2'd0;
    reg [23:0] acc = 24'd0;
    always @(posedge tclk) begin
        run_s <= {run_s[1:0], run};
        acc   <= {q1, q0, acc[23:8]};              // Q0: rising-edge sample, Q1: the following falling edge
        bcnt  <= run_s[2] ? bcnt + 2'd1 : 2'd0;
        o_we  <= run_s[2] && bcnt == 2'd3;
        o_wd  <= {q1, q0, acc};
    end

    /* verilator lint_off UNUSEDSIGNAL */
    wire _unused = &{1'b0, df};
    /* verilator lint_on UNUSEDSIGNAL */
endmodule
