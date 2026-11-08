#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export XDG_CONFIG_HOME="${TMPDIR:-/tmp}/ff1-config" XDG_CACHE_HOME="${TMPDIR:-/tmp}/ff1-cache" XDG_DATA_HOME="${TMPDIR:-/tmp}/ff1-data"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" reports preview
kicad-cli sch erc --exit-code-violations -o reports/ERC.rpt hardware/FF1_Control_A0.kicad_sch
kicad-cli sch export netlist --format kicadxml -o reports/netlist.xml hardware/FF1_Control_A0.kicad_sch
kicad-cli pcb drc --schematic-parity --exit-code-violations -o reports/DRC.rpt hardware/FF1_Control_A0.kicad_pcb
/usr/bin/python3 scripts/validate_design.py
