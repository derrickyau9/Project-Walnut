# Offline calibration package store

This standard-library component records per-sensor, versioned calibration
**bookkeeping for HF-Sim**. It does not calibrate hardware or apply corrections
to captures. It supports only an explicit `uncalibrated` marker or statistics
from the deterministic synthetic Bayer generator. No real sensor calibration,
color transform, defect map, flat-field correction or measured accuracy is
inferred. Synthetic packages keep `hardware_calibrated: false` and a fixed
warning in their checksummed payload.

```python
from camera.calibration import CalibrationStore, create_synthetic_package

store = CalibrationStore('var/calibration', system_image_dir='system')
package = create_synthetic_package('sim-sensor-001', 'dark-flat', '1.0.0')
store.put(package, expected_sensor_id='sim-sensor-001')
backup = store.export_package('sim-sensor-001', 'dark-flat', '1.0.0',
                              'var/backups/sim-sensor-001.json')
store.import_package(backup, expected_sensor_id='sim-sensor-001')
```

The caller supplies a persistent directory; no source-tree or system-image
directory is selected by default. On a deployed system, place it on separate
persistent storage and pass the actual system image directory to enforce
disjoint roots. Reopening the same data root preserves packages through process
and image changes. This code does not provision partitions or validate a real
system update process.

Packages live at `DATA/SENSOR/PACKAGE/VERSION.json`. IDs and versions are
case-sensitive portable strings of 1-64 characters, starting with an ASCII
letter or digit and then using letters, digits, `.`, `_`, or `-`; trailing dots
and Windows device names are forbidden. Use stable sensor identities, and use
a new package version for changed content. Reimporting identical content is
idempotent; overwriting the same identity with different content is rejected.
Use `create_uncalibrated_package(sensor_id, package_id, version, reason=...)`
when no measurements exist. The synthetic factory summarizes even RGGB frames
from 2 through 256 pixels on each side; these bounded demo samples are not a
full-frame calibration or throughput measurement.

The exact v1 JSON schema rejects unknown/missing fields, duplicate keys,
non-finite numbers, invalid statistics and unsupported provenance. The
`checksum` is `sha256:` followed by a lowercase SHA-256 of the remaining object
serialized as sorted-key, compact, ASCII-escaped JSON without NaN or Infinity.
Whitespace and object key order in an imported file do not affect validation.
The checksum detects accidental corruption; it is not a signature or an
authenticity guarantee. Loads and lists verify integrity and sensor/path
identity every time and fail visibly on corruption.

Import validates the complete bounded JSON document before writing. A sibling
temporary file is flushed and synchronized, then atomically hard-linked to its
immutable final name; concurrent writers cannot overwrite a committed version.
Export uses the same staging approach and atomically replaces the selected
external file. Local filesystem hard-link and atomic-rename support is required;
unsupported operations fail without a partial final file. Staging files left by
an interrupted process are ignored, never treated as valid packages. Symbolic
links and Windows reparse points in package/export paths are rejected. The store
is not a privileged sandbox against concurrent filesystem replacement, and
these simulator tests do not establish arbitrary power-loss durability on
camera media.

`CalibrationError.code` distinguishes `invalid_package`, `checksum_mismatch`,
`sensor_mismatch`, `identity_mismatch`, `invalid_identifier`, `invalid_path`,
`conflict`, `not_found`, and `storage_error`. Creating or importing a package
does not activate it or apply it to the pixel pipeline. Unbound captures retain
`calibration_id: synthetic-uncalibrated`. Authorities that support explicit
binding must independently verify and durably pin the selected snapshot under
their control lease; see [the binding format](BINDING.md).

## Immutable binding artifacts

```python
from camera.calibration import BindingStore, create_binding

bindings = BindingStore('var/calibration-bindings', system_image_dir='system')
artifact = bindings.put(create_binding(package))  # requires sensor_id HF-Sim
bindings.export_binding(artifact['binding_id'], 'var/backups/binding.json')
```

`BindingStore.snapshot(packages, package_id, version)` revalidates a package
directly from `CalibrationStore` before committing a detached immutable
artifact. Its content ID, exact package bytes and checksums survive source-store
changes and process restart. Missing or corrupt requested artifacts fail
visibly; the helper never substitutes a default. `read_binding`,
`validate_binding` and `binding_package` are also exported. `binding_package`
returns a detached standalone package with a derived canonical checksum;
retain the artifact's `package_checksum` separately as the exact wire-byte
trace digest.

These functions are unprivileged artifact helpers and own no active-device
selection. They do not modify RAW samples. Both `hardware_calibrated` and
`pixel_corrections_applied` are always false. The checked-in
[fixtures](fixtures) cover uncalibrated, finite-extreme synthetic and
512-codepoint Unicode payloads for independent native validation.
