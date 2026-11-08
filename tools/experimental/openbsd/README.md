# OpenBSD development experiments

These project-owned scripts preserve the build and review work behind the experimental OpenBSD migration: RAM payload preparation, one-shot EFI chaining, device-tree checks, kernel compilation and isolated filesystem proofs.

They are development records, **not a general-purpose firmware installer**. Their original contents are preserved, including fixed artifact identities and workstation paths. Read their input requirements before attempting a reproduction. The existence of a script is not evidence that its hardware behavior passed.

`SOURCE-MANIFEST.json` records each original working path, size and SHA-256. Downloaded operating-system source trees, compilers, VM disks, generated evidence, private RAM payloads, credentials and device backups are excluded. The maintained RAM-kernel ELF inspector is `system/openbsd/kernel_elf.py`; current board-side experimental entry points are under `system/openbsd/raspberrypi/`.

实验代码保留了开发时的固定输入与工作路径，不属于通用安装器。虚拟机验证、源码分析和真实硬件结果需要分开理解；不能据此认定无线、启动回退或看门狗已在新系统上完成。
