#!/bin/sh
# Testbenches in iverilog:
#   tb_stream - stream path (afifo, sfifo, test source, drop counters)
#   tb_tpiu   - tpiu_rx on a synthetic TPIU stream (ITM counter), decoded by tools/tpiu_decode.py
#   sh rtl/sim/run.sh            (+vcd to dump tb_stream.vcd)
# Tools: OSS CAD Suite in $OSS (default ~/.local/opt/oss-cad-suite/bin) or PATH; python3.
set -e
cd "$(dirname "$0")/.."
OSS=${OSS:-$HOME/.local/opt/oss-cad-suite/bin}
tool() { if [ -x "$OSS/$1" ]; then echo "$OSS/$1"; else command -v "$1"; fi; }
IV=$(tool iverilog); VVP=$(tool vvp)
T=../tools/tpiu_decode.py

echo "== tb_stream"
"$IV" -g2012 -s tb -o sim/tb_stream.vvp sim/tb_stream.v stream.v afifo.v
"$VVP" -n sim/tb_stream.vvp "$@" | tee sim/tb_stream.log
grep -q "^PASS" sim/tb_stream.log

echo "== tb_tpiu"
python3 $T encode --out sim/stream.hex
"$IV" -g2012 -s tb -o sim/tb_tpiu.vvp sim/tb_tpiu.v sim/gowin_models.v tpiu/tpiu_rx.v
"$VVP" -n sim/tb_tpiu.vvp
python3 $T decode sim/tpiu_words.hex --expect 160
