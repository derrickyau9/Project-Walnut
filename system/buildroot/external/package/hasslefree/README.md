# Native application package

Select `BR2_PACKAGE_HASSLEFREE_CAMERA` in the `DAOS_CAMERA` br2-external tree.
The board defconfig must enable the Qt5 declarative Quick, Quick Controls2 and
OpenGL library options. PyQt5's upstream Buildroot recipe requires the OpenGL
library to build the QtQuick binding, even when the application uses Qt's
software scene-graph renderer at runtime. A rendering library does not prove
physical GPU/display support.

The local source site is **only** `camera/native`. Buildroot's target compiler,
C++17 flags, target sysroot and linker build `hf-camerad` and
`hf-native-worker`; no previous Debian-linked ELF or host test executable is
copied into the image. The small `native.make` intentionally avoids the
upstream project CMake file's unconditional desktop test targets.

`install_runtime.py` copies an enumerated source set into `/opt/hasslefree`
and records its exact source hashes. It rejects links, oversized sources,
unexpected existing app files and inputs edited during installation. Generated
data, hardware imports, tests, build output, caches and credentials are outside
the allowed set. Native binaries install in `/usr/bin`; their pre-final-strip
hashes are explicitly labeled and must not be confused with the final image's
ELF commitments. Init/data mounts, camera-user permissions and the QEMU boot
recipe belong to the board integration.

The application remains synthetic. Its new contracts/workflow/control patterns
do not establish reuse of the existing DaOS framework. The installed rights
inventory and provenance remain explicit: project-wide public redistribution
grants are pending, nlohmann/json retains its MIT notice, and SQLite retains its
upstream public-domain notice. The package is marked non-redistributable for
Buildroot legal-info rather than inventing a GPL grant. PyQt/Qt and all system
packages retain their separate upstream terms and source obligations.

After changing application files outside the native local source site, use
`make hasslefree-camera-reinstall` before final image generation. Freeze source
inputs while installing; a full clean target is required after removed runtime
files or an unexpected target entry. Board validation and reproducibility must
use the final image, not only the installed application manifest.
