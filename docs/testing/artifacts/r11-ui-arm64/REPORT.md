# R11 actual ARM64 reference UI review — historical development layout

The final R11 source was executed as UID1000 on the existing FreeBSD 15.1-RELEASE-p6 ARM64 QEMU reference guest using Python 3.12.15, Qt 5.15.19 and PyQt 5.15.11. The offscreen/software renderer is separate from the R10 custom appliance image and from physical display or sensor validation. The user rejected this desktop-style layout; these artifacts do not represent an accepted camera product UI.

All 70 model, app, QML and legacy integration tests passed with no skips (86.995 seconds in unittest; 90.911 seconds including the bounded harness). This includes intentional native captures in isolated test fixtures. The staged source hashes were identical before and after testing. The exact 39-file source snapshot and manifest are retained here.

The separate review runner was invoked once, took one synthetic capture, and produced all 13 original PNGs. Original UUID: `bb435cce-d84b-4d2c-8eed-ef41dc580638`. It confirmed terminal capture state `complete`, retained review and export before failing its final interface preference recreation assertion. The daemon closed with exit0 and no QML warnings. The report correctly remains `passed:false`; no capture or UI correction was retried.

The actual saved preferences are grid enabled, preview brightness100, theme light. `13-compact-camera-dark.png` is a misleading runner filename: its actual pixels and stored theme are light, and it is not dark-theme proof. The read-only evidence establishes that the intended dark action was not reflected, but cannot determine whether automation input delivery or UI handling caused it. No source fix was attempted.

The restricted network boot failed SSH banner/readiness checks and cleanly closed after its360-second budget. One separately authorized standard NAT boot became ready in52.271 seconds; it had guest egress enabled and only a loopback SSH hostforward. No package install or download was performed. The final guest shutdown was clean, QEMU exited0, and immutable original base/seed/firmware-code hashes remained unchanged. Existing R10 data was preserved. No private keys, DNG bytes or databases were copied into this evidence directory.

See `verification.json`, original `review.json`, `tests.log`, both boot directories, and the unmodified PNGs. SHA256 receipts establish byte consistency, not publisher authenticity. The extra exploratory read-only export-directory diagnostic was inconclusive and is not counted as a passing check.
