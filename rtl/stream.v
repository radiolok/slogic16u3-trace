// Stream path: trace words (tclk) -> afifo, 512 words -> sfifo, 2^FIFO_LOG2 words (pclk) -> valid/ready output,
// one word per clock. Test source (src 1): a counter in pclk, one word every rate clocks (0 = 1), written straight
// into sfifo and dropped when it is full (gdrop). Trace words are dropped only at afifo (tdrop, Gray-coded to pclk).
module stream #(
    parameter FIFO_LOG2 = 12
) (
    input  wire        tclk,
    input  wire        t_we,
    input  wire [31:0] t_wd,
    input  wire        pclk,
    input  wire        run,
    input  wire [1:0]  src,
    input  wire [15:0] rate,
    input  wire        clr,
    output wire        o_valid,
    output wire [31:0] o_data,
    input  wire        i_ready,
    output reg  [31:0] o_w_out = 32'd0,   // words sent
    output reg  [31:0] o_w_in = 32'd0,    // trace words written to sfifo
    output wire [31:0] o_tdrop,           // trace words dropped (afifo full) since clear
    output reg  [31:0] o_gdrop = 32'd0,   // test words dropped (sfifo full)
    output reg  [31:0] o_tfreq = 32'd0,   // tclk cycles per 2^20 pclk
    output wire [31:0] o_fill             // [31:16] sfifo peak since clear, [15:0] now
);
    localparam FD = 1 << FIFO_LOG2;

    // ---- tclk domain: drops and frequency counter, passed over in Gray code
    wire        a_full;
    reg  [31:0] tdrop = 32'd0, tdrop_g = 32'd0;
    reg  [31:0] tcnt = 32'd0, tcnt_g = 32'd0;
    always @(posedge tclk) begin
        if (t_we && a_full) tdrop <= tdrop + 32'd1;
        tdrop_g <= tdrop ^ (tdrop >> 1);
        tcnt <= tcnt + 32'd1;
        tcnt_g <= tcnt ^ (tcnt >> 1);
    end
    wire        a_empty;
    wire [31:0] a_rd;
    wire        a_re;
    afifo #(.AW(9)) u_afifo (.wclk(tclk), .we(t_we), .wd(t_wd), .wfull(a_full),
        .rclk(pclk), .re(a_re), .rd(a_rd), .rempty(a_empty));

    // ---- sfifo
    reg  [31:0] smem [0:FD-1];
    reg  [FIFO_LOG2:0] swp = 0, srp = 0;
    wire [FIFO_LOG2:0] scnt = swp - srp;
    reg  [31:0] smem_q = 32'd0;
    reg         a_rv = 1'b0;                          // the afifo word is on a_rd this cycle
    assign a_re = !a_empty && !a_rv && scnt < FD - 2;  // backpressure: trace drops only at afifo
    always @(posedge pclk) a_rv <= a_re;
    reg  [15:0] tgen_div = 16'd0;
    reg  [31:0] tgen_val = 32'd0;
    reg         tgen_we = 1'b0;
    always @(posedge pclk) begin
        tgen_we <= 1'b0;
        if (!(run && src == 2'd1)) begin tgen_div <= 16'd0; tgen_val <= 32'd0; end
        else if (tgen_div == 16'd0) begin
            tgen_div <= (rate == 16'd0) ? 16'd0 : rate - 16'd1;
            tgen_we  <= 1'b1;
        end else tgen_div <= tgen_div - 16'd1;
        if (tgen_we) tgen_val <= tgen_val + 32'd1;
    end
    wire        s_we = a_rv || (tgen_we && scnt != FD);
    wire [31:0] s_wd = a_rv ? a_rd : tgen_val;

    // ---- output: 2-word queue behind the 1-cycle BSRAM read, a word per clock without bubbles
    reg  [31:0] oq0 = 32'd0, oq1 = 32'd0;
    reg  [1:0]  oqn = 2'd0;
    reg         rd_q = 1'b0;                          // smem_q holds a new word
    wire        take = i_ready && oqn != 2'd0;
    wire        s_re = swp != srp && ({1'b0, oqn} + {2'b0, rd_q} - {2'b0, take} <= 3'd1);
    always @(posedge pclk) begin
        if (s_we) begin smem[swp[FIFO_LOG2-1:0]] <= s_wd; swp <= swp + 1'b1; end
        if (s_re) begin smem_q <= smem[srp[FIFO_LOG2-1:0]]; srp <= srp + 1'b1; end
        rd_q <= s_re;
        case ({take, rd_q})
            2'b11: if (oqn == 2'd1) oq0 <= smem_q; else begin oq0 <= oq1; oq1 <= smem_q; end
            2'b10: begin oq0 <= oq1; oqn <= oqn - 2'd1; end
            2'b01: begin if (oqn == 2'd0) oq0 <= smem_q; else oq1 <= smem_q; oqn <= oqn + 2'd1; end
            default: ;
        endcase
    end
    assign o_valid = oqn != 2'd0;
    assign o_data  = oq0;

    // ---- counters (pclk)
    function [31:0] gray2bin(input [31:0] gv);
        integer k;
        begin
            gray2bin[31] = gv[31];
            for (k = 30; k >= 0; k = k - 1) gray2bin[k] = gray2bin[k + 1] ^ gv[k];
        end
    endfunction
    reg [31:0] tdrop_s1 = 32'd0, tdrop_s2 = 32'd0, tdrop_b = 32'd0, tdrop0 = 32'd0;
    reg [31:0] tcnt_s1 = 32'd0, tcnt_s2 = 32'd0, tcnt_b = 32'd0, tcnt_l = 32'd0;
    reg [19:0] win = 20'd0;
    reg [FIFO_LOG2:0] smax = 0;
    always @(posedge pclk) begin
        {tdrop_s2, tdrop_s1} <= {tdrop_s1, tdrop_g};
        {tcnt_s2, tcnt_s1} <= {tcnt_s1, tcnt_g};
        tdrop_b <= gray2bin(tdrop_s2);
        tcnt_b  <= gray2bin(tcnt_s2);
        win <= win + 20'd1;
        if (&win) begin o_tfreq <= tcnt_b - tcnt_l; tcnt_l <= tcnt_b; end
        if (o_valid && i_ready) o_w_out <= o_w_out + 32'd1;
        if (a_rv) o_w_in <= o_w_in + 32'd1;
        if (tgen_we && scnt == FD) o_gdrop <= o_gdrop + 32'd1;
        if (scnt > smax) smax <= scnt;
        if (clr) begin o_w_out <= 32'd0; o_w_in <= 32'd0; o_gdrop <= 32'd0; smax <= 0; tdrop0 <= tdrop_b; end
    end
    assign o_tdrop = tdrop_b - tdrop0;
    assign o_fill  = {{(15 - FIFO_LOG2){1'b0}}, smax, {(15 - FIFO_LOG2){1'b0}}, scnt};
endmodule
