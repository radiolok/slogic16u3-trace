#!/bin/sh
# Verilator -Wall lint of the RTL; fails on any warning.
#   sh rtl/sim/lint.sh
# Gowin primitives and usb/board blocks are port stubs (gowin_stubs.v). Disabled: PROCASSINIT only - initial
# values in declarations are deliberate on Gowin FPGAs (state after configuration, as in the reference).
# The USB stack (tops Usb3Top, Usb3Device) uses the waivers from its Veryl.toml plus PINCONNECTEMPTY: this is how
# Veryl emits an unconnected output (`o_lfsr: _`).
# Tools: OSS CAD Suite in $OSS (default ~/.local/opt/oss-cad-suite/bin) or PATH; veryl in PATH.
set -e
cd "$(dirname "$0")/.."
OSS=${OSS:-$HOME/.local/opt/oss-cad-suite/bin}
V=$([ -x "$OSS/verilator" ] && echo "$OSS/verilator" || command -v verilator)
R=usb/usb3
(cd $R && veryl build > /dev/null 2>&1) || { (cd $R && veryl build); exit 1; }
SV="$R/usb3_pkg.sv $R/usb3_crc_pkg.sv $R/usb3_descriptor_rom.sv $R/usb3_scrambler.sv $R/usb3_rx_fifo.sv
    $R/usb3_rx_parser.sv $R/usb3_tx_framer.sv $R/usb3_ltssm.sv $R/usb3_link.sv $R/usb3_top.sv $R/usb3_device.sv"
FAIL=0
report() {  # report <name> <warnings>
    if [ -n "$2" ]; then echo "$1:"; echo "$2"; FAIL=1; else echo "$1: clean"; fi
}
warn() { grep -E "^%(Warning|Error)" | grep -v "Exiting due" | sort -u || true; }

O=$($V --lint-only -Wall -Wno-PROCASSINIT --top-module tpiu_rx sim/gowin_stubs.v tpiu/tpiu_rx.v 2>&1 | warn)
report "tpiu_rx" "$O"
O=$($V --lint-only -Wall -Wno-PROCASSINIT --top-module usb3d sim/gowin_stubs.v $SV usb3d.v stream.v afifo.v \
    tpiu/tpiu_rx.v 2>&1 | warn | grep -E "usb3d\.v|stream\.v|afifo\.v|tpiu_rx\.v|gowin_stubs" || true)
report "usb3d" "$O"
W=$(sed -n '/compile_args/,/]/p' $R/Veryl.toml | grep -oE '"-Wno-[A-Z]+"' | tr -d '"' | tr '\n' ' ')
for t in Usb3Top Usb3Device; do
    O=$($V --lint-only -Wall $W -Wno-PROCASSINIT -Wno-DECLFILENAME -Wno-PINCONNECTEMPTY --top-module $t $SV 2>&1 | warn)
    report "$t" "$O"
done
exit $FAIL
