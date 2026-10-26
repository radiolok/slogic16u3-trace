# USB 3.0 device stack

The USB 3.0 SuperSpeed device stack by Kenta Ida, taken from
[ciniml/fpga_samples](https://github.com/ciniml/fpga_samples) at commit `41354eb`, Boost Software License 1.0
([LICENSE-BSL-1.0](LICENSE-BSL-1.0)). Written in [Veryl](https://veryl-lang.org); `veryl build` turns it into
SystemVerilog (the build and lint scripts do this, the `.sv` files are not committed).

| Path | From upstream | Contents |
|---|---|---|
| `usb3/` | `rtl/usb3/` | LTSSM, link layer, packet parser and framer, EP0 and EP1 (`usb3_device`), descriptor ROM; upstream tests (`veryl test`) and spec notes |
| `board/jtag_status.v`, `board/pll_ref125.v` | `eda/slogic16u3_usb3/src/slogic16u3/` | status registers on GW_JTAG ER1/ER2; PHY reference clock PLL |
| `board/serdes.csr` | `eda/slogic16u3_usb3/.../usb3_ln0/serdes.csr` | static SerDes quad Q0 setup for the USB 3.0 PHY (lane 0, QPLL0, REFCLK0 125 MHz), used with `set_csr` |
| `ip/` | — | Gowin IP generator inputs; see below |

## Changes

Marked `rln:` in the source.

| File | Change |
|---|---|
| `usb3_ltssm.veryl` | pclk 125 MHz timing on GW5AT-15: compares of `pb_sent` / `tx_after_rx` against their limits become flags registered with the increment; outside Polling.LFPS these counters stay at 0 instead of being cleared on entry; `tseq_cnt` / `skp_cnt` and `idle_sent` clear in the first clock of a state (`st_chg`, a register) instead of from the next-state logic |
| `usb3_rx_parser.veryl` | timing: CRC-32 in two stages (the DPP word is registered, CRC follows a clock later); the DPP verdict (`o_dpp_end` / `o_dpp_err`) one more clock later, from the registered CRC |
| `gen_descriptor_rom.py` → `usb3_descriptor_rom.veryl` | string descriptors: `rln-trace` / `SLogic16U3 trace` / `0001` (VID:PID 1209:0001) |
| `Veryl.toml` | `-Wno-FUNCTIMECTL -Wno-IMPLICITSTATIC -Wno-ZERODLY`: Verilator 5.05x does not build the upstream tests otherwise |

The upstream tests under `usb3/test/` (link, frame, device) fail on Verilator 5.05x with the unmodified upstream
RTL as well, so they are not part of CI; the stack itself is verified on hardware.

## Gowin IP

The encrypted Gowin IP (USB 3.0 PHY, upar arbiter) is not redistributed. `rtl/build.sh` generates it from the
installed Gowin EDA:

| File | Used for |
|---|---|
| `ip/serdes.mod` | `GowinModGen` input for the SerDes wrapper `SerDes_Top`; `-USB3_0_PHY_TopEnableDRP true` is required, otherwise the wrapper has no upar arbiter |
| `ip/usb3_0_phy_name.v`, `ip/usb3_0_phy_top_define.vh` | module name and lane define for synthesising the PHY body into `usb3_0_phy.vg` |
| `ip/upar_define.vh`, `ip/upar_parameter.vh` | the same for the upar arbiter, `upar_arbiter.vg` |
| `ip/syn_ip.tcl` | `gw_sh` script: one IP into a netlist |
