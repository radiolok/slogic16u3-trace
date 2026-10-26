// tpiu_rx testbench: plays a nibble stream (one hex nibble per line, from `tools/tpiu_decode.py encode`) on
// TRACED0..3 at both TRACECLK edges and writes the received words to OUT, one hex word per line.
// The stream is decoded and checked by tools/tpiu_decode.py; see run.sh.
`timescale 1ns/1ps
module tb;
    parameter IN = "sim/stream.hex";
    parameter OUT = "sim/tpiu_words.hex";
    parameter N = 4096;                 // max nibbles
    reg [3:0] nib [0:N-1];
    integer n = 0, i, fi, fo, words = 0;
    reg [3:0] v;
    reg tclk = 0;
    reg [3:0] d = 4'h0;
    reg run = 0;
    wire we; wire [31:0] wd;
    tpiu_rx dut (.tclk(tclk), .traced(d), .dly({4{8'd40}}), .run(run), .o_we(we), .o_wd(wd));

    always @(posedge tclk) if (we) begin $fwrite(fo, "%08x\n", wd); words = words + 1; end

    initial begin
        fi = $fopen(IN, "r");
        while (n < N && $fscanf(fi, "%h\n", v) == 1) begin nib[n] = v; n = n + 1; end
        $fclose(fi);
        fo = $fopen(OUT, "w");
        // TRACECLK 50 MHz; data change mid-way between edges: a nibble per edge, the first one at a rising edge.
        // Before the stream the port idles as TPIU does in continuous mode: half-word syncs FF 7F.
        for (i = 0; i < 32; i = i + 2) begin
            if (i == 8) run = 1;
            #5 d = 4'hF;                  #5 tclk = 1;
            #5 d = i % 4 ? 4'h7 : 4'hF;   #5 tclk = 0;
        end
        for (i = 0; i < n; i = i + 2) begin
            #5 d = nib[i];     #5 tclk = 1;
            #5 d = nib[i + 1]; #5 tclk = 0;
        end
        d = 4'hF;
        repeat (16) begin #10 tclk = 1; #10 tclk = 0; end
        $fclose(fo);
        $display("tb_tpiu: %0d nibbles in, %0d words out", n, words);
        $finish;
    end
endmodule
