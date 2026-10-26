#!/bin/sh
# Build usb3d with Gowin EDA: build/usb3d.fs (SRAM over JTAG) and build/usb3d.bin (flash over DFU).
#
#   sh rtl/build.sh                               all four place_option variants, the fastest pclk wins
#   PLACE="3 4" sh rtl/build.sh                   only these place_option values
#   PNR_EXTRA="replicate_resources=1" sh rtl/build.sh   extra PnR options for every variant
#
# GOWIN    Gowin EDA IDE directory (default: Windows install seen from WSL)
# WORK     scratch directory; for Windows Gowin it must be on a Windows drive
#
# Gowin IP is generated from the installed EDA and never committed: the SerDes wrapper by GowinModGen
# (usb/ip/serdes.mod), the encrypted USB 3.0 PHY and upar arbiter bodies by synthesising them into netlists.
# The USB stack is Veryl, converted with `veryl build` (veryl in PATH).
#
# pclk 125 MHz closes in some placements only and Gowin PnR has no seed, so place_option 1..4 run in parallel
# and the best one is kept.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$HERE/../build
GW=${GOWIN:-/mnt/c/Gowin/Gowin_V1.9.12.04_x64/IDE}
B=${WORK:-/mnt/c/Users/Public/slogic16u3-trace}
if [ -x "$GW/bin/gw_sh.exe" ]; then
    X=.exe; native() { wslpath -m "$1"; }     # Windows Gowin from WSL
else
    X=; native() { echo "$1"; }
fi
IPC=$GW/ipcore/SERDES_IP
N=usb3d
U=$HERE/usb
PLACE=${PLACE:-"1 2 3 4"}
mkdir -p "$B/ip" "$B/upar" "$B/phy" "$OUT"

# IP, once (delete $WORK to regenerate)
if [ ! -f "$B/ip/serdes.v" ]; then
    sed "s|^-path .*|-path $(native "$B/ip")/|" "$U/ip/serdes.mod" > "$B/ip/serdes.mod"
    (cd "$B" && "$GW/bin/GowinModGen$X" -do "$(native "$B/ip/serdes.mod")" > /dev/null)
fi
if [ ! -f "$B/upar_arbiter.vg" ]; then
    cp "$IPC"/data/Upar_Arbiter/* "$U/ip/syn_ip.tcl" "$B/upar/"
    cp "$U/ip/upar_define.vh" "$B/upar/define.vh"
    cp "$U/ip/upar_parameter.vh" "$B/upar/parameter.vh"
    (cd "$B/upar" && "$GW/bin/gw_sh$X" syn_ip.tcl upar_arbiter "~upar_arbiter_wrap.SerDes_Top" \
        upar_arbiter_wrap.v upar_arbiter.v > syn.log)
    cp "$B/upar/impl/gwsynthesis/upar_arbiter.vg" "$B/"
fi
if [ ! -f "$B/usb3_0_phy.vg" ]; then
    cp "$IPC"/IPlib/USB30PHY/data/* "$U/ip/usb3_0_phy_name.v" "$U/ip/usb3_0_phy_top_define.vh" \
       "$U/ip/syn_ip.tcl" "$B/phy/"
    (cd "$B/phy" && "$GW/bin/gw_sh$X" syn_ip.tcl usb3_0_phy USB3_0_PHY_Top usb3_0_phy_top.v usb3_0_phy.v > syn.log)
    cp "$B/phy/impl/gwsynthesis/usb3_0_phy.vg" "$B/"
fi

# USB stack: Veryl -> SystemVerilog
(cd "$U/usb3" && veryl build > /dev/null 2>&1)
SV="usb3_pkg usb3_crc_pkg usb3_scrambler usb3_rx_fifo usb3_rx_parser usb3_tx_framer usb3_ltssm usb3_link usb3_top
    usb3_descriptor_rom usb3_device"

# one PnR variant in its own directory: build_one <label> <options>; prints "<label> <pclk MHz>"
build_one() {
    D="$B/${N}_$1"
    rm -rf "${D:?}"; mkdir -p "$D"
    for f in $SV; do cp "$U/usb3/$f.sv" "$D/"; done
    cp "$HERE/usb3d.v" "$HERE/stream.v" "$HERE/afifo.v" "$HERE/tpiu/tpiu_rx.v" "$HERE/usb3d.cst" "$HERE/usb3d.sdc" "$D/"
    cp "$U/board/pll_ref125.v" "$U/board/jtag_status.v" "$U/board/serdes.csr" \
       "$B/ip/serdes.v" "$B/upar_arbiter.vg" "$B/usb3_0_phy.vg" "$GW/data/ipcores/gw_jtag.v" "$D/"
    { echo "set_device GW5AT-LV15MG132C1/I0 -device_version B"
      for f in usb3d.v stream.v afifo.v tpiu_rx.v pll_ref125.v; do echo "add_file $f"; done
      for f in $SV; do echo "add_file $f.sv"; done
      for f in jtag_status.v serdes.v upar_arbiter.vg usb3_0_phy.vg gw_jtag.v usb3d.cst usb3d.sdc; do echo "add_file $f"; done
      echo "set_csr serdes.csr"
      echo "set_option -top_module $N"
      echo "set_option -output_base_name $N"
      echo "set_option -verilog_std sysv2017"
      for o in done ready cpu i2c sspi; do echo "set_option -use_${o}_as_gpio 1"; done
      # multiboot to the golden image (factory DFU loader at 0x800000): the MODE button always gets back to DFU
      echo "set_option -multi_boot 1"
      echo "set_option -multiboot_spi_flash_address 800000"
      echo "set_option -multiboot_address_width 24"
      echo "set_option -bit_format bin"
      for kv in $2; do echo "set_option -${kv%%=*} ${kv#*=}"; done
      echo "run all"; } > "$D/gowin.tcl"
    if (cd "$D" && "$GW/bin/gw_sh$X" gowin.tcl > build.log 2>&1) && ! grep -q "ERROR" "$D/build.log"; then
        echo "$1 $(grep -m1 -oE "pclk +125\.000\(MHz\) +[0-9.]+" "$D/impl/pnr/$N.tr" | awk '{print $3}')"
    else
        echo "$1 failed"; tail -5 "$D/build.log" >&2
    fi
}

R="$B/results.txt"
: > "$R"
for p in $PLACE; do
    build_one "p$p" "place_option=$p route_option=2 serdesRetiming=1 ${PNR_EXTRA:-}" >> "$R" &
done
wait
sort -k2 -rn "$R" | sed 's/^/  /'
BEST=$(grep -v failed "$R" | sort -k2 -rn | head -1)
[ -n "$BEST" ] || { echo "no variant built"; exit 1; }
set -- $BEST
rm -f "$OUT/$N.fs" "$OUT/$N.bin"
cp "$B/${N}_$1/impl/pnr/$N.fs" "$B/${N}_$1/impl/pnr/$N.bin" "$OUT/"
chmod 644 "$OUT/$N.fs" "$OUT/$N.bin"
OK=$(awk -v f="$2" 'BEGIN { print (f >= 125) ? "met" : "NOT met" }')
echo "build/$N.fs, build/$N.bin <- variant $1: pclk $2 MHz, timing $OK"
