# System integration and host simulator deployment

The [Linux host path](linux/README.md) has an actual POSIX native build, a
nonroot service template, immutable simulator bundle staging, a capture/restart
health check and an executable host A/B trial model. It has been exercised on
Debian Linux in WSL. It neither changes an installed service nor selects a real
boot slot automatically.

The [generic ARM64 Qt image recipe](targets/README.md) additionally boots actual
ARM Python/Qt and the native synthetic camera with a read-only root and private
persistent data disk. First/resume QML capture, review/export and same-ID checks
pass. This virtual target does not replace the board-specific checklist below.

There is no tested camera BSP, bootloader configuration, recovery image, device
tree, target update agent or power-control firmware here. The checklist below
still governs real hardware; the host model does not implement those pieces.

Partition system slots A/B separately from configuration, calibration and
photographs. Version the board, FPGA, kernel, device tree, camera service,
calibration format and update manifest; explicitly reject incompatible mixes.
Require a verified boot path and a recovery mechanism for the chosen hardware.
Authenticity, rollback and ownership policy require a separate decision and
must not prevent the owner from installing their own builds by design.

For an update: verify package compatibility and integrity, install the inactive
slot, persist its trial status, reboot and run bounded health checks covering
storage and the capture path, then mark success. On failure or power loss the
bootloader's documented fallback must remain usable. Never reformat or overwrite
calibration and photos as part of a system update. Exercise repeated power cuts
at each update stage with the actual bootloader, storage and filesystem.

Monitor independent acquisition progress, DMA completion, storage commit and
user interface responsiveness. The deadline must include commanded exposure,
mode-specific readout and bounded processing/writing; long exposure alone is
not proof of failure. Recover locally first, track recovery budgets persistently
enough to prevent reboot loops, and preserve evidence. An independent MCU
recovery channel and basic on-device shooting are product targets, not present
capabilities. Confirm them only by fault injection on the actual assembled unit.
