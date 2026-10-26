// Async FIFO: Gray-coded pointers, BSRAM storage (rd is valid one clock after re)
module afifo #(
    parameter AW = 9
) (
    input  wire        wclk,
    input  wire        we,
    input  wire [31:0] wd,
    output wire        wfull,
    input  wire        rclk,
    input  wire        re,
    output reg  [31:0] rd = 32'd0,
    output wire        rempty
);
    reg [31:0] mem [0:(1 << AW) - 1];
    reg [AW:0] wb = 0, wg = 0, rb = 0, rg = 0;
    reg [AW:0] rg_w1 = 0, rg_w2 = 0, wg_r1 = 0, wg_r2 = 0;
    wire [AW:0] wb_n = wb + 1'b1, rb_n = rb + 1'b1;
    assign wfull  = wg == {~rg_w2[AW:AW-1], rg_w2[AW-2:0]};
    assign rempty = rg == wg_r2;
    always @(posedge wclk) begin
        {rg_w2, rg_w1} <= {rg_w1, rg};
        if (we && !wfull) begin
            mem[wb[AW-1:0]] <= wd;
            wb <= wb_n;
            wg <= wb_n ^ (wb_n >> 1);
        end
    end
    always @(posedge rclk) begin
        {wg_r2, wg_r1} <= {wg_r1, wg};
        if (re && !rempty) begin
            rd <= mem[rb[AW-1:0]];
            rb <= rb_n;
            rg <= rb_n ^ (rb_n >> 1);
        end
    end
endmodule
