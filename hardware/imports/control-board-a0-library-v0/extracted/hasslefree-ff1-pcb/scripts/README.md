# Reproduction

Requirements: KiCad 9, `/usr/bin/python3` with its matching `pcbnew`, NumPy and SciPy. These were present in the cloud workspace. No new software installation was performed.

Run `./scripts/check.sh` to check the existing deliverable without rewriting it.

`build_design.py` overwrites the schematic, local libraries, project and PCB with the source-defined placement. `route_board.py` accepts an unrouted board only and routes the low-speed circuit. Do not rerun either over hand edits without preserving a copy. Rebuilding is not needed to open/edit the delivered KiCad project.

Regeneration: `/usr/bin/python3 scripts/build_design.py`, then `/usr/bin/python3 scripts/route_board.py`, then `./scripts/check.sh`. The routing is a basic conservative grid search, not an SI/EMI or manufacturing optimizer. KiCad DRC and independent engineering review remain necessary after any changes. All screenshots and STEP/PDF exports must be regenerated after circuit/layout edits.
