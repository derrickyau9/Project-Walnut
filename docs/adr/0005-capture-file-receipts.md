# Capture file identity and publication receipts

Status: implemented in R8 for the native synthetic authority and Python
reference simulator. This is not a hardware/media or adversarial sandbox claim.

## Problem

A plausible DNG filename and successful writer return do not establish that the
file being published is the one produced. Actual owned-directory probes found
that a redirected `photos` directory could receive a capture outside its owner
root and still be marked complete. Replacing a closed staging file with invalid
bytes could also produce false completion. Python private staging directories
could themselves be redirected during Bayer generation.

## Principle and interface

The independent implementation uses open-object identity, bounded byte checks,
exclusive creation and create-only publication. It uses no OEM camera firmware,
resources or private interfaces. Directory identity is recorded at startup and
checked again at admission and I/O boundaries. A writer receipt identifies the
actual opened regular file, its expected length and serialized contents.
Completion requires the published object to match that receipt. A failure after
publication has been attempted can remain `unknown`; it must not trigger replay.

## Implementation

Native `DngOutputReceipt` is internal to the C++ core. The writer captures the
opened descriptor identity before close and computes the serialized-file FNV
checksum while writing. The authority reopens a no-follow/nonblocking regular
handle, verifies identity, size, checksum and stable stat values, synchronizes
that handle, and checks staging/final objects around publication. FNV is a
corruption/replacement check, not cryptographic authentication. Cleanup removes
only a matching complete owned file; changed or incomplete staging is preserved.

The Python reference records root/photos and private-stage identities. Its
Python writer retains a descriptor-derived identity and bounded SHA256 of the
produced bytes, validates DNG structure and identity, and checks the file before
and after publication. Mixed native-worker output is also structurally inspected
and bound after the worker returns; it does not inherit the authority's internal
C++ receipt. Cleanup refuses redirected/replaced storage, leaving orphaned
original staging for inspection. Neither implementation promises atomic
isolation against a hostile same-user process racing every namespace operation.

## Executed evidence

[Native R8 report](../../camera/native/evidence/round8/REPORT.md) records Windows
directory/junction checks, Linux directory checks and six actual interposed
staging cases including FIFO, symlink, same-size replacement, in-place mutation
and foreign final output. Both native platforms passed their core/authority
assertions. [Reference tests](../../tests/test_reference_capture_paths.py)
exercise startup/admission redirection, post-publication uncertainty, staging
redirection, produced-file replacement and in-place byte alteration with restored
timestamps. [Final R8 evidence](../testing/artifacts/round8/verification.json)
records combined regressions and actual ARM64 first/resume capture results.

Physical card removal, filesystem-specific power cuts and recovery remain
separate acceptance gates. A checksum is not a signature, and this decision does
not authenticate an arbitrary untrusted source/runtime package.
