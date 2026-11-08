# Deferred Buildroot branch

The user switched the active OS request to DaOS BSD Unix on 2026-11-05.
No Buildroot toolchain, kernel or root filesystem build was started.
The downloaded official release and local partial configuration are preserved.

The external package is incomplete and untested. In particular its current
`package/hasslefree` directory does not match its `HASSLEFREE_CAMERA` generic
package naming. The referenced board/init files were not implemented. Do not
treat this directory as a working Buildroot image or use it as BSD evidence.

The active target is documented under `system/freebsd/`.
