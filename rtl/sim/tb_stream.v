// stream testbench: trace words through afifo and the test source, consumer with random ready.
// Checks: data is continuous or its gaps equal the drop counters; a word per clock when ready = 1.
`timescale 1ns/1ps
module tb;
    reg tclk = 0, pclk = 0;
    always #8 tclk = ~tclk;      // 62.5 MHz
    always #4 pclk = ~pclk;      // 125 MHz
    reg run = 0, clr = 0; reg [1:0] src = 0; reg [15:0] rate = 0;
    reg t_we = 0; reg [31:0] t_wd = 0; reg [1:0] tph = 0; reg t_on = 0;
    always @(posedge tclk) begin            // a trace word every 4 clocks, value = counter
        tph <= tph + 1;
        t_we <= t_on && tph == 3;
        if (t_on && tph == 3) t_wd <= t_wd + 1;
    end
    reg ready = 0; integer pr = 50;        // ready probability, %
    wire valid; wire [31:0] data;
    wire [31:0] w_out, w_in, tdrop, gdrop, tfreq, fill;
    stream #(.FIFO_LOG2(12)) dut (.tclk(tclk), .t_we(t_we), .t_wd(t_wd), .pclk(pclk), .run(run), .src(src),
        .rate(rate), .clr(clr), .o_valid(valid), .o_data(data), .i_ready(ready),
        .o_w_out(w_out), .o_w_in(w_in), .o_tdrop(tdrop), .o_gdrop(gdrop), .o_tfreq(tfreq), .o_fill(fill));
    integer got = 0, gaps = 0, lost = 0, errors = 0, bub = 0; reg [31:0] expv = 0; reg first = 1;
    reg stall = 0; reg [31:0] gen_n = 0;
    always @(posedge pclk) begin
        ready <= !stall && ($urandom % 100) < pr;
        if (valid && ready) begin
            got = got + 1;
            if (!first && data != expv) begin
                gaps = gaps + 1;
                if (data > expv) lost = lost + (data - expv);
                else begin errors = errors + 1; if (errors < 5) $display("  ERROR: went back to %h after %h", data, expv - 1); end
            end
            first = 0; expv = data + 1;
        end
        if (ready && !valid && src == 1 && run) bub = bub + 1;
    end
    task reset_rx; begin got = 0; gaps = 0; lost = 0; first = 1; bub = 0; end endtask
    task check(input integer name, input [31:0] drops); begin
        $display("%0d: received %0d, gaps %0d, missing by gaps %0d, drop counter %0d, w_out %0d, peak fill %0d",
                 name, got, gaps, lost, drops, w_out, fill[31:16]);
        if (lost != drops || errors) begin $display("  ERROR: gaps do not match the counter"); errors = errors + 1; end
    end endtask
    initial begin
        if ($test$plusargs("vcd")) begin $dumpfile("sim/tb_stream.vcd"); $dumpvars(0, tb); end
        repeat (20) @(posedge pclk);
        // 1. trace, ready 50 % (output 4x faster than input): no drops
        run = 1; src = 0; t_on = 1; pr = 50;
        repeat (40000) @(posedge pclk);
        t_on = 0; repeat (2000) @(posedge pclk);
        $display("1: trace, ready 50 %%"); check(1, tdrop);
        if (gaps || tdrop) errors = errors + 1;
        // 2. trace, consumer stalled for 600 us: 4K words in sfifo + 512 in afifo, the rest is dropped
        reset_rx; clr = 1; @(posedge pclk); clr = 0;
        t_on = 1; stall = 1; repeat (75000) @(posedge pclk); stall = 0;
        repeat (20000) @(posedge pclk); t_on = 0; repeat (5000) @(posedge pclk);
        $display("2: trace, consumer stalled for 600 us"); check(2, tdrop);
        if (tdrop == 0) begin $display("  ERROR: no drops"); errors = errors + 1; end
        // 3. test source rate 1, ready 100 %: a word per clock, no bubbles
        reset_rx; run = 0; @(posedge pclk); clr = 1; src = 1; rate = 1; pr = 100; @(posedge pclk); clr = 0; run = 1;
        repeat (20) @(posedge pclk); bub = 0;
        repeat (20000) @(posedge pclk);
        $display("3: test source rate 1, ready 100 %%: bubbles %0d", bub);
        if (bub) errors = errors + 1;
        run = 0; repeat (100) @(posedge pclk);
        check(3, gdrop);
        // 4. test source rate 1, ready 30 %: sfifo drops, gaps = gdrop
        reset_rx; clr = 1; pr = 30; @(posedge pclk); clr = 0; run = 1;
        repeat (60000) @(posedge pclk); gen_n = dut.tgen_val + dut.tgen_we; run = 0; pr = 100; repeat (20000) @(posedge pclk);
        lost = lost + (gen_n - expv);   // tail: words dropped after the last received one show no gap
        $display("4: test source rate 1, ready 30 %%"); check(4, gdrop);
        if (gdrop == 0) errors = errors + 1;
        // (frequency counter: 2^20 pclk window = 8.4 ms, not simulated)
        if (errors) $display("FAIL: %0d errors", errors); else $display("PASS");
        $finish;
    end
endmodule
