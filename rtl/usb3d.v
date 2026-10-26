// usb3d: parallel trace from SLogic16U3 inputs to the PC over USB 3.0 SuperSpeed.
// USB stack: usb/usb3 (Usb3Top + Usb3Device); PHY, resets and JTAG status follow ciniml's reference top
// (K. Ida, BSL-1.0).
//
//   TRACECLK (CH12) + TRACED0..3 (CH8..CH11) -> tpiu_rx: 32-bit words in the TRACECLK domain
//     -> afifo, 512 words (TRACECLK -> pclk) -> sfifo, 2^FIFO_LOG2 words -> Usb3Device EP1 IN, a word per clock
//   Test source (src 1): a word counter in pclk, one word every RATE clocks; when sfifo is full the word is
//   dropped but the counter keeps going, so gaps seen by the host equal the drop counter.
//   Vendor request 0x02 = 1 replaces EP1 IN data with PRBS15 (tools/debug/u3prbs.py).
//
// Registers via Usb3Device: vendor OUT 0x03 (wIndex = address, wValue = 16 bits), vendor IN 0x04 (32 bits).
//   write 0  CTRL: [0] run, [2:1] source (0 trace, 1 test counter), [3] = 1 clears the counters
//         1  RATE: test source period, pclk cycles (0 = 1)
//         2  IODELAY TRACED0 [7:0], TRACED1 [15:8], ~12.5 ps steps
//         3  IODELAY TRACED2 [7:0], TRACED3 [15:8]
//   read  0  "RLU3" (0x33554C52)
//         1  [15:0] CTRL, [23:16] RATE[7:0], [31:24] version
//         2  TRACECLK cycles per 2^20 pclk window (8.39 ms)
//         3  words sent to EP1 IN (trace and test source)
//         4  trace words dropped: afifo full (TRACECLK domain, via Gray code)
//         5  test source words dropped: sfifo full
//         6  sfifo: [15:0] now, [31:16] peak since clear
//         7  IODELAY: [7:0] TRACED0 ... [31:24] TRACED3
//         8  [4:0] LTSSM state, [5] U0, [6] link configured, [7] device configured, [14:8] address
//         9  trace words written to sfifo
// JTAG: ER1 - link status (reference layout), ER2 - packet header log (128 entries); see tools/debug/u3status.py.
module usb3d #(
    parameter FIFO_LOG2 = 12   // sfifo: 4096 words = 16 KB (8 BSRAM)
) (
    input  wire tck_pad_i,
    input  wire tms_pad_i,
    input  wire tdi_pad_i,
    output wire tdo_pad_o,

    input  wire [12:8] ch,      // CH8..CH11: TRACED0..3, CH12: TRACECLK
    input  wire usb_cmp_dp,     // H3 / H2: D+ against VREF
    input  wire usb_cmp_dp_n,
    input  wire usb_cmp_dn,     // M2 / M1: D- against VREF
    input  wire usb_cmp_dn_n,
    input  wire usb_dxp,        // K3, K2, P2, P1: USB 2.0 dx / term balls (inputs, no pull)
    input  wire usb_dxn,
    input  wire usb_tdp,
    input  wire usb_tdn,
    output wire usb_pullup,     // C14 (off): SuperSpeed only
    output wire xo_en,          // D14: 125 MHz oscillator enable
    output wire led_r           // G14: in U0
);
    localparam [7:0] VER = 8'd1;

    wire osc;   // ~51.7 MHz (measured)
    OSCA #(.FREQ_DIV(4)) u_osc (.OSCOUT(osc), .OSCEN(1'b1));
    assign xo_en = 1'b1;
    assign usb_pullup = 1'b0;
    wire cmp_dp, cmp_dn;
    TLVDS_IBUF u_cmp_dp (.I(usb_cmp_dp), .IB(usb_cmp_dp_n), .O(cmp_dp));
    TLVDS_IBUF u_cmp_dn (.I(usb_cmp_dn), .IB(usb_cmp_dn_n), .O(cmp_dn));

    // ---------------------------------------------------------------- ref_clk, PHY reset (as in the reference)
    reg [20:0] seq = 21'd0;
    always @(posedge osc) if (!seq[20]) seq <= seq + 21'd1;
    wire ref_clk /* synthesis syn_keep=1 */, ref_lock;
    pll_ref125 u_pll (.lock(ref_lock), .clkout0(ref_clk), .clkin(osc));
    reg [2:0] start_s = 3'd0;
    reg [16:0] phy_rst_cnt = 17'd0;
    wire phy_resetn = phy_rst_cnt[16];
    always @(posedge ref_clk) begin
        start_s <= {start_s[1:0], seq[20] & ref_lock};
        if (!start_s[2]) phy_rst_cnt <= 17'd0;
        else if (!phy_resetn) phy_rst_cnt <= phy_rst_cnt + 17'd1;
    end

    // ---------------------------------------------------------------- PHY
    wire pclk /* synthesis syn_keep=1 */;
    wire [31:0] rx_data, tx_data; wire [3:0] rx_k, tx_k; wire rx_valid, rx_elecidle, phy_status, power_present;
    wire [2:0] rx_status; wire [1:0] power_down; wire tx_elecidle, tx_detectrx, rx_polarity, rx_termination;
    SerDes_Top u_serdes (
        .USB3_0_PHY_Top_pclk(pclk),
        .USB3_0_PHY_Top_PipeRxData(rx_data),
        .USB3_0_PHY_Top_PipeRxDataK(rx_k),
        .USB3_0_PHY_Top_PipeRxDataValid(rx_valid),
        .USB3_0_PHY_Top_RxElecIdle(rx_elecidle),
        .USB3_0_PHY_Top_RxStatus(rx_status),
        .USB3_0_PHY_Top_PhyStatus(phy_status),
        .USB3_0_PHY_Top_PowerPresent(power_present),
        .USB3_0_PHY_Top_ref_clk(ref_clk),
        .USB3_0_PHY_Top_phy_resetn(phy_resetn),
        .USB3_0_PHY_Top_PipeTxData(tx_data),
        .USB3_0_PHY_Top_PipeTxDataK(tx_k),
        .USB3_0_PHY_Top_TxDetectRx_loopback(tx_detectrx),
        .USB3_0_PHY_Top_TxElecIdle(tx_elecidle),
        .USB3_0_PHY_Top_RxPolarity(rx_polarity),
        .USB3_0_PHY_Top_RxEqTraining(1'b0),
        .USB3_0_PHY_Top_RxTermination(rx_termination),
        .USB3_0_PHY_Top_TxOnesZeros(1'b0),
        .USB3_0_PHY_Top_PowerDown(power_down),
        .USB3_0_PHY_Top_ElasticityBufferMode(1'b0));

    reg [2:0] prn_s = 3'd0;
    reg [12:0] prst_cnt = 13'd0;
    always @(posedge pclk) begin
        prn_s <= {prn_s[1:0], phy_resetn};
        if (!prn_s[2]) prst_cnt <= 13'd0;
        else if (!prst_cnt[12]) prst_cnt <= prst_cnt + 13'd1;
    end
    wire prst = !prst_cnt[12];

    // ---------------------------------------------------------------- link + protocol
    wire rx_hp_valid; wire [95:0] rx_hp_hdr;
    wire dpp_start, dpp_valid, dpp_end, dpp_err; wire [31:0] dpp_data; wire [3:0] dpp_be;
    wire tx_hp_valid, tx_dpp, tx_hp_ready, tx_hp_idle; wire [95:0] tx_hp_hdr; wire [10:0] tx_dpp_len; wire [12:0] tx_dpp_base;
    wire dpp_rd; wire [12:0] dpp_addr; wire [31:0] dpp_rdata;
    wire [4:0] ltssm_state; wire in_u0, u0_entry, hot_reset, warm_reset, link_configured;
    wire dev_configured, dev_reset, dbg_event, dbg_strobe; wire [63:0] dbg_state; wire [6:0] dev_address; wire [15:0] vendor_reg;
    wire reg_wr; wire [7:0] reg_addr; wire [15:0] reg_wdata; reg [31:0] reg_rdata;
    wire epw_valid, epw_ready; wire [31:0] epw_data;
    wire ep_rx_valid, ep_tx_ready; wire [7:0] ep_rx_data;

    Usb3Top u_usb3 (.i_clk(pclk), .i_rst(prst),
        .i_rx_data(rx_data), .i_rx_k(rx_k), .i_rx_valid(rx_valid), .i_rx_status(rx_status), .i_rx_elecidle(rx_elecidle),
        .i_phy_status(phy_status), .i_power_present(1'b1),
        .o_tx_data(tx_data), .o_tx_k(tx_k), .o_tx_elecidle(tx_elecidle), .o_tx_detectrx(tx_detectrx),
        .o_power_down(power_down), .o_rx_polarity(rx_polarity), .o_rx_termination(rx_termination),
        .o_rx_hp_valid(rx_hp_valid), .o_rx_hp_hdr(rx_hp_hdr),
        .o_dpp_start(dpp_start), .o_dpp_valid(dpp_valid), .o_dpp_data(dpp_data), .o_dpp_be(dpp_be), .o_dpp_end(dpp_end), .o_dpp_err(dpp_err),
        .i_tx_hp_valid(tx_hp_valid), .i_tx_hp_hdr(tx_hp_hdr), .i_tx_dpp(tx_dpp), .i_tx_dpp_len(tx_dpp_len), .i_tx_dpp_base(tx_dpp_base),
        .o_tx_hp_ready(tx_hp_ready), .o_tx_hp_idle(tx_hp_idle),
        .o_dpp_rd(dpp_rd), .o_dpp_addr(dpp_addr), .i_dpp_data(dpp_rdata),
        .o_ltssm_state(ltssm_state), .o_in_u0(in_u0), .o_u0_entry(u0_entry), .o_hot_reset(hot_reset), .o_warm_reset(warm_reset),
        .o_configured(link_configured));

    Usb3Device #(.STREAM_FLUSH_US(1000)) u_dev (.i_clk(pclk), .i_rst(prst),
        .i_in_u0(in_u0), .i_hot_reset(hot_reset), .i_warm_reset(warm_reset),
        .i_rx_hp_valid(rx_hp_valid), .i_rx_hp_hdr(rx_hp_hdr),
        .i_dpp_start(dpp_start), .i_dpp_valid(dpp_valid), .i_dpp_data(dpp_data), .i_dpp_be(dpp_be), .i_dpp_end(dpp_end), .i_dpp_err(dpp_err),
        .o_tx_hp_valid(tx_hp_valid), .o_tx_hp_hdr(tx_hp_hdr), .o_tx_dpp(tx_dpp), .o_tx_dpp_len(tx_dpp_len), .o_tx_dpp_base(tx_dpp_base),
        .i_tx_hp_ready(tx_hp_ready), .i_dpp_rd(dpp_rd), .i_dpp_addr(dpp_addr), .o_dpp_data(dpp_rdata),
        .o_configured(dev_configured), .o_address(dev_address), .o_reset(dev_reset), .o_dbg_event(dbg_event), .o_dbg_strobe(dbg_strobe), .o_dbg_state(dbg_state),
        .o_vendor_reg(vendor_reg), .i_vendor_status({8'h55, 3'b0, ltssm_state, 7'b0, link_configured, 8'h03}),
        .o_reg_wr(reg_wr), .o_reg_addr(reg_addr), .o_reg_wdata(reg_wdata), .i_reg_rdata(reg_rdata),
        // EP1 OUT is drained; the byte-wide EP1 IN input is unused
        .o_rx_valid(ep_rx_valid), .o_rx_data(ep_rx_data), .i_rx_ready(1'b1),
        .i_tx_valid(1'b0), .i_tx_data(8'd0), .o_tx_ready(ep_tx_ready),
        .i_txw_valid(epw_valid), .i_txw_data(epw_data), .o_txw_ready(epw_ready));

    // ---------------------------------------------------------------- registers (pclk)
    reg [15:0] ctrl = 16'd0, rate = 16'd0;
    reg [31:0] dls = 32'd0;     // IODELAY: quasi-static, used in TRACECLK without sync
    reg        clr = 1'b0;      // pulse: clear counters
    always @(posedge pclk) begin
        clr <= 1'b0;
        if (reg_wr) case (reg_addr)
            8'd0: begin ctrl <= {12'd0, 1'b0, reg_wdata[2:0]}; clr <= reg_wdata[3]; end
            8'd1: rate <= reg_wdata;
            8'd2: dls[15:0] <= reg_wdata;
            8'd3: dls[31:16] <= reg_wdata;
            default: ;
        endcase
    end
    wire run = ctrl[0];
    wire [1:0] src = ctrl[2:1];

    // ---------------------------------------------------------------- trace capture (TRACECLK domain)
    wire tclk = ch[12];  // N9: GCLKT_2, global clock net
    wire        a_we;
    wire [31:0] a_wd;
    tpiu_rx u_tpiu (.tclk(tclk), .traced(ch[11:8]), .dly(dls), .run(run && src == 2'd0), .o_we(a_we), .o_wd(a_wd));

    // ---------------------------------------------------------------- stream: afifo -> sfifo -> EP1 IN
    wire        prbs_on;
    wire        st_valid;
    wire [31:0] st_data;
    wire [31:0] w_out, w_in, tdrop, gdrop, tfreq;
    wire [31:0] sfill;
    stream #(.FIFO_LOG2(FIFO_LOG2)) u_stream (
        .tclk(tclk), .t_we(a_we), .t_wd(a_wd),
        .pclk(pclk), .run(run), .src(src), .rate(rate), .clr(clr),
        .o_valid(st_valid), .o_data(st_data), .i_ready(epw_ready && !prbs_on),
        .o_w_out(w_out), .o_w_in(w_in), .o_tdrop(tdrop), .o_gdrop(gdrop), .o_tfreq(tfreq), .o_fill(sfill));

    // PRBS15 on vendor request 0x02 = 1, as in the reference
    function [14:0] prbs15_adv32(input [14:0] s);
        integer k; reg [14:0] t;
        begin
            t = s;
            for (k = 0; k < 32; k = k + 1) t = {t[13:0], t[14] ^ t[13]};
            prbs15_adv32 = t;
        end
    endfunction
    function [31:0] prbs15_word(input [14:0] s);
        integer k; reg [14:0] t; reg [31:0] b;
        begin
            t = s;
            for (k = 0; k < 32; k = k + 1) begin b[k] = t[14] ^ t[13]; t = {t[13:0], t[14] ^ t[13]}; end
            prbs15_word = b;
        end
    endfunction
    wire       prbs_mode = vendor_reg == 16'h0001;
    reg        prbs_mode_d = 1'b0;
    reg [14:0] prbs_s = 15'h7FFF;
    reg [31:0] prbs_w = 32'd0;
    always @(posedge pclk) begin
        prbs_mode_d <= prbs_mode;
        if (prbs_mode && !prbs_mode_d) begin prbs_s <= 15'h7FFF; prbs_w <= prbs15_word(15'h7FFF); end
        else if (prbs_mode && epw_ready) begin prbs_s <= prbs15_adv32(prbs_s); prbs_w <= prbs15_word(prbs15_adv32(prbs_s)); end
    end
    assign prbs_on   = prbs_mode;
    assign epw_valid = prbs_mode ? prbs_mode_d : st_valid;
    assign epw_data  = prbs_mode ? prbs_w : st_data;

    // ---------------------------------------------------------------- register read
    always @* begin
        case (reg_addr)
            8'd0: reg_rdata = 32'h33554C52;   // "RLU3"
            8'd1: reg_rdata = {VER, rate[7:0], ctrl};
            8'd2: reg_rdata = tfreq;
            8'd3: reg_rdata = w_out;
            8'd4: reg_rdata = tdrop;
            8'd5: reg_rdata = gdrop;
            8'd6: reg_rdata = sfill;
            8'd7: reg_rdata = dls;
            8'd8: reg_rdata = {17'd0, dev_address, dev_configured, link_configured, in_u0, ltssm_state};
            8'd9: reg_rdata = w_in;
            default: reg_rdata = 32'd0;
        endcase
    end

    // ---------------------------------------------------------------- ER1 status (reference layout)
    reg [15:0] cnt_u0 = 16'd0, cnt_hp = 16'd0, cnt_reset = 16'd0, cnt_dpp_err = 16'd0, cnt_physt = 16'd0;
    reg [4:0]  st_max = 5'd0, st_d = 5'd0;
    reg [31:0] st_seen = 32'd0;
    reg [79:0] st_trace = 80'd0;
    reg [7:0]  cnt_trans = 8'd0;
    reg        elec_d = 1'b1;
    reg [15:0] cnt_lfps = 16'd0;
    always @(posedge pclk) begin
        if (u0_entry) cnt_u0 <= cnt_u0 + 16'd1;
        if (rx_hp_valid) cnt_hp <= cnt_hp + 16'd1;
        if (hot_reset || warm_reset) cnt_reset <= cnt_reset + 16'd1;
        if (dpp_end && dpp_err) cnt_dpp_err <= cnt_dpp_err + 16'd1;
        if (phy_status) cnt_physt <= cnt_physt + 16'd1;
        if (ltssm_state > st_max) st_max <= ltssm_state;
        st_seen[ltssm_state] <= 1'b1;
        st_d <= ltssm_state;
        if (ltssm_state != st_d) begin st_trace <= {st_trace[74:0], ltssm_state}; cnt_trans <= cnt_trans + 8'd1; end
        elec_d <= rx_elecidle;
        if (elec_d && !rx_elecidle) cnt_lfps <= cnt_lfps + 16'd1;
    end
    reg [9:0]  snap_div = 10'd0;
    reg [307:0] snap = 308'd0;
    wire [307:0] live = {
        w_out,                                                  // [351:320] (EP1 OUT bytes in the reference)
        st_trace,                                               // [319:240]
        st_seen,                                                // [239:208]
        cnt_lfps, cnt_physt, cnt_dpp_err, cnt_reset, cnt_hp, cnt_u0, // [207:112]
        vendor_reg,                                             // [111:96]
        cnt_trans,                                              // [95:88]
        1'b0, dev_address,                                      // [87:80]
        2'b0, power_present, rx_elecidle, phy_status, rx_status, // [79:72]
        3'b0, st_max,                                           // [71:64]
        3'b0, ltssm_state,                                      // [63:56]
        1'b0, power_down, tx_elecidle, tx_detectrx, rx_termination, rx_polarity, rx_valid, // [55:48]
        dev_configured, link_configured, in_u0, prst};          // [47:44]
    always @(posedge pclk) begin snap_div <= snap_div + 10'd1; if (&snap_div) snap <= live; end
    reg [3:0] pdiv = 4'd0 /* synthesis syn_preserve=1 */;
    always @(posedge pclk) pdiv <= pdiv + 4'd1;
    reg [2:0] pds = 3'd0; reg [21:0] gate = 22'd0; reg [19:0] pn = 20'd0, pf = 20'd0;
    always @(posedge osc) begin
        pds <= {pds[1:0], pdiv[3]}; gate <= gate + 22'd1;
        if (&gate) begin pf <= pn; pn <= 20'd0; end
        else if (pds[1] && !pds[2]) pn <= pn + 20'd1;
    end
    reg [31:0] up = 32'd0; reg [15:0] upl = 16'd0;
    always @(posedge osc) begin upl <= upl + 16'd1; if (&upl) up <= up + 32'd1; end
    reg [5:0] pads_s = 6'd0;
    always @(posedge osc) pads_s <= {cmp_dn, cmp_dp, usb_tdn, usb_tdp, usb_dxn, usb_dxp};
    reg [15:0] cnt_com = 16'd0, cnt_dec = 16'd0, cnt_disp = 16'd0;
    wire [3:0] com_k = {rx_k[3] && rx_data[31:24] == 8'hBC, rx_k[2] && rx_data[23:16] == 8'hBC,
                        rx_k[1] && rx_data[15:8] == 8'hBC, rx_k[0] && rx_data[7:0] == 8'hBC};
    always @(posedge pclk) begin
        if (rx_valid && |com_k) cnt_com <= cnt_com + 16'd1;
        if (rx_status == 3'd4) cnt_dec <= cnt_dec + 16'd1;
        if (rx_status == 3'd7) cnt_disp <= cnt_disp + 16'd1;
    end
    wire [431:0] cap = {cnt_disp, cnt_dec, cnt_com, up, snap, pf, pads_s, phy_resetn, ref_lock, 16'h5B31};

    // ---------------------------------------------------------------- header log (ER2)
    // Every received (except ITP) and sent header since the first U0: 128 entries
    // {dir (1 = tx), 24-bit pclk timestamp, DW2..DW0}, then the log stops. ER2 write (bit 191 = 1),
    // [190:160] = w2_data: [30] = 1 rearms (mode [29:28]; 3 = ring of EP1 IN packets until Usb3Device
    // o_dbg_event plus 64 after), otherwise [6:0] selects the entry for the next read.
    // The reference's PIPE word capture (1024 x 76 bits) is left out to save ~5 BSRAM.
    wire        w2; wire [30:0] w2_data;
    reg  [1:0]  tmode = 2'd1;
    reg         rearm_t = 1'b0;
    always @(posedge osc) if (w2 && w2_data[30]) begin tmode <= w2_data[29:28]; rearm_t <= ~rearm_t; end
    reg  [2:0]  rearm_s = 3'd0;
    always @(posedge pclk) rearm_s <= {rearm_s[1:0], rearm_t};
    wire        rearm = rearm_s[2] != rearm_s[1];
    reg  [1:0]  tmode_p = 2'd1;
    always @(posedge pclk) if (rearm) tmode_p <= tmode;
    reg  [120:0] hlog [0:127];
    reg  [7:0]   hwp = 8'd0;
    reg  [23:0]  hts = 24'd0;
    reg          hrun = 1'b0, hwrap = 1'b0, htrig = 1'b0, hfrz = 1'b0;
    reg  [6:0]   hpost = 7'd0;
    wire         hring = tmode_p == 2'd3;
    wire         hin_rx = rx_hp_hdr[39] && rx_hp_hdr[43:40] == 4'd1;
    wire         hin_tx = tx_hp_hdr[39] && tx_hp_hdr[43:40] == 4'd1;
    wire         hev_rx = rx_hp_valid && rx_hp_hdr[4:0] != 5'h0C && (!hring || hin_rx);
    wire         hev_tx = tx_hp_valid && tx_hp_ready && (!hring || hin_tx);
    always @(posedge pclk) begin
        hts <= hts + 24'd1;
        if (in_u0) hrun <= 1'b1;
        if (rearm) begin hwp <= 8'd0; hrun <= in_u0; hwrap <= 1'b0; htrig <= 1'b0; hfrz <= 1'b0; hpost <= 7'd0; end
        else begin
            if (hring && dbg_event) htrig <= 1'b1;
            if (hrun && !hfrz && (hring || !hwp[7]) && (hev_rx || hev_tx || (hring && dbg_strobe))) begin
                hlog[hwp[6:0]] <= hev_rx ? {1'b0, hts, rx_hp_hdr} : (hring && dbg_strobe) ? {1'b1, hts, dbg_state, 32'h1F} :
                                  {1'b1, hts, tx_hp_hdr};
                hwp <= {1'b0, hwp[6:0] + 7'd1} | {hring ? 1'b0 : hwp[6:0] == 7'd127, 7'd0};
                if (hring && hwp[6:0] == 7'd127) hwrap <= 1'b1;
                if (hring && htrig) begin hpost <= hpost + 7'd1; if (hpost == 7'd63) hfrz <= 1'b1; end
            end
        end
    end
    reg  [6:0]   tidx = 7'd0;
    reg  [120:0] tq = 121'd0;
    reg  [6:0]   tq_idx = 7'd0;
    always @(posedge osc) begin
        if (w2 && !w2_data[30]) tidx <= w2_data[6:0];
        tq <= hlog[tidx];
        tq_idx <= tidx;
    end
    // ER2 (192 bits): [15:0] 0x5B34, [22:16] entry index, [143:23] entry, [151:144] pointer / entry count
    // (128 = full), [153:152] mode, [154] ring wrapped, [155] log stopped
    wire [191:0] cap2 = {36'd0, hfrz, hwrap, tmode_p, hwp, tq, tq_idx, 16'h5B34};

    jtag_status #(.L(432), .L2(192)) u_jtag (.tck_pad_i(tck_pad_i), .tms_pad_i(tms_pad_i), .tdi_pad_i(tdi_pad_i),
        .tdo_pad_o(tdo_pad_o), .osc(osc), .cap(cap), .cap2(cap2), .o_w2(w2), .o_w2_data(w2_data));

    assign led_r = in_u0;

    // intentionally unused
    /* verilator lint_off UNUSEDSIGNAL */
    wire _unused = &{1'b0, tx_hp_idle, dev_reset, ep_rx_valid, ep_rx_data, ep_tx_ready, w2_data[27:7]};
    /* verilator lint_on UNUSEDSIGNAL */
endmodule
