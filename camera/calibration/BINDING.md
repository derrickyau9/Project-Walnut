# HF-Sim calibration binding wire format v1

This artifact snapshots synthetic statistics or an explicit uncalibrated
marker. It identifies traceable package content and applies no pixel
corrections. It is not measured calibration, a signature, or proof of hardware
authenticity. The expected sensor identity for the current camera authorities
is the case-sensitive ASCII string `HF-Sim`.

The outer JSON object has exactly seven fields:

| Field | Required value |
| --- | --- |
| `binding_schema_version` | Integer `1`; a Boolean or floating-point value is invalid. |
| `binding_id` | `cb1-` plus 64 lowercase hexadecimal SHA-256 characters. |
| `sensor_id` | `HF-Sim`, matching the package payload. |
| `package_checksum` | `sha256:` plus 64 lowercase hexadecimal characters. |
| `package_payload` | Nonempty ASCII JSON string, at most 65,536 ASCII bytes. |
| `hardware_calibrated` | Boolean `false`. |
| `pixel_corrections_applied` | Boolean `false`. |

The complete external UTF-8 JSON document is limited to 131,072 bytes. Reject
duplicate keys in both outer JSON and the decoded payload, including nested
objects. Reject non-finite constants, invalid Unicode, unsupported versions,
extra or missing fields, and any claim of measured calibration or corrections.
No `verified` field or caller assurance substitutes for validation.

Let `P` be the exact bytes obtained by ASCII-encoding `package_payload` after
decoding the outer JSON string. The two hashes are:

```text
package_checksum = "sha256:" + lowercase_hex(SHA256(P))
binding_id = "cb1-" + lowercase_hex(SHA256(D || P))
D = ASCII bytes "HF-Sim calibration binding v1" followed by exactly one LF byte
```

There is no BOM, NUL terminator, CR byte or length prefix in `D`. `||` means
byte concatenation. The outer object's whitespace, key order and escaping do
not affect these hashes. All other envelope fields are constrained by the
schema and validated against the payload; changing one cannot bypass those
checks. Hashes establish content integrity, not author identity.

The payload parses to the existing strict package schema v1 **excluding its
`checksum` field**. All remaining package fields are mandatory and retain the
same types, numeric bounds, exact provenance strings and fixed warning as the
standalone store. Check the full schema even when both hashes match. A checksum
inside the payload is an extra field and is rejected. Only `synthetic` and
`uncalibrated` statuses are supported. Hardware-calibrated is always Boolean
false. Sensor identity must match the envelope and the authority's expected
sensor independently.

The emitter uses the existing sorted-key, compact, ASCII-escaped JSON encoding
for stable created artifacts. **Wire validators hash the supplied bytes and do
not reserialize floating-point values.** Any bounded, valid ASCII JSON spelling
with the exact strict package semantics is permitted. Alternate whitespace,
object order or numeric spelling creates a distinct content ID and checksum.
This avoids claiming that Python and C++ floating-point serializers have
identical output. For an artifact created from a canonical standalone package,
the package's original checksum equals the wire `package_checksum`. For other
permitted spellings, the checksum of a reconstructed canonical standalone
package may differ. Preserve the wire checksum in capture trace metadata.

Uncalibrated reasons are 1 through 512 Unicode scalar values after JSON escape
decoding, counted as codepoints rather than UTF-8 bytes or UTF-16 units. Lone
surrogates and codepoints below U+0020 are rejected. Leading or trailing
whitespace is forbidden using the following complete set of remaining
whitespace codepoints: U+0020, U+0085, U+00A0, U+1680, U+2000 through U+200A,
U+2028, U+2029, U+202F, U+205F and U+3000. Interior Unicode whitespace at or above
U+0020 is permitted. The v1 standalone Python validator also rejects lone
surrogates so it can produce valid cross-language payloads.

`BindingStore` persists immutable artifacts by their content ID and revalidates
every requested load. It creates no active selection and has no control-lease
authority. The capture authority must validate the artifact itself, enforce its
lease and idle requirements when changing selection, and durably store the
selected artifact. It must snapshot that artifact when accepting a capture so
later activation cannot rewrite an earlier capture's trace. Missing, corrupt,
mismatched or partially persisted selections must fence capture without silent
fallback. A content ID identifies a snapshot, not a unique activation event;
activation-event history needs a separate authoritative sequence if required.

The frozen fixtures are `binding-uncalibrated.json`, `binding-synthetic.json`
and `binding-unicode.json` in `fixtures/`. The synthetic fixture uses exposure
4,294,967,295 microseconds and gain 0.000001 within the simulator schema's
allowed bounds; it is not a measured operating point. The Unicode fixture has
512 scalar values, including 510 supplementary-plane emoji. Tests independently
recompute fixture hashes with Python `hashlib`; the native implementation must
also validate them directly and test its SHA-256 against known vectors.
