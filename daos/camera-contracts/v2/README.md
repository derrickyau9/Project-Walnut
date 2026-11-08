# Bound synthetic capture records

API1.1 advertises `capture_record_versions:[1,2]`. Existing unbound captures keep
the v1 shape and `calibration_id:synthetic-uncalibrated`; the v1 schema is not
silently changed. Opting into a verified calibration binding creates v2 records,
identified by `capture_record_version:2`. The major HTTP control route remains
`/api/v1`; this discriminator versions capture metadata independently.

`POST /api/v1/calibration/bind` takes exactly token, epoch and binding artifact.
`POST /api/v1/calibration/clear` takes exactly token and epoch. Both require an
idle authority and a live control lease. The authority independently validates
the complete snapshot and sensor, commits selection before acknowledgement,
and pins that exact artifact in each accepted record. Bad state fences capture;
there is no fallback to an uncalibrated capture after corruption. A clear is an
explicit new selection, not a rewrite of historical records.

The artifact wire rules are [specified separately](../../../camera/calibration/BINDING.md).
Schema validation is only structural: runtime validators must verify exact
payload hashes, strict v1 package semantics and record/binding/sensor identity.
JSON requests retain the existing64KiB transport bound; storing a larger binding
artifact does not guarantee it fits an activation RPC. No raw pixels are JSON.

Both flags remain false: these packages contain synthetic statistics or an
explicit uncalibrated marker. Selection records provenance and does not apply
pixel correction, a measured color matrix, a LUT or a hardware calibration.
The DNG retains the pinned calibration ID; querying its UUID also returns the
immutable snapshot. Changing or clearing active selection never changes the
original request or re-executes it.
