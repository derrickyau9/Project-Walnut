# Offline basic photography - round 8

The basic CLI now covers capabilities, current state, original-ID queries,
bounded retained-image pages, verified asset metadata and create-only DNG export.
It uses either the reference simulator or an explicitly selected native
authority, without importing DaOS workflows, Qt, a browser or a model service.
All pixels and parameter results remain synthetic.

```sh
python -m camera.camerad --data-dir var/basic capabilities
python -m camera.camerad --data-dir var/basic capture --exposure-us 12300
python -m camera.camerad --data-dir var/basic status
python -m camera.camerad --data-dir var/basic list --limit 10
python -m camera.camerad --data-dir var/basic query SAVED_UUID
python -m camera.camerad --data-dir var/basic asset SAVED_UUID
python -m camera.camerad --data-dir var/basic export SAVED_UUID --output /existing/directory/copy.dng
```

For the native authority add `--authority native --native-daemon /path/to/hf-camerad`
before the subcommand. `--backend native --native-worker PATH` is the separate
mixed mode, where the Python authority delegates pixel generation to a worker.
Incompatible or silently ignored backend selections are now rejected before
creating a data directory. Each independent authority still needs its own data
directory; the CLI does not attach to an already-running owner's journal.

Capture prints its UUID before submitting. After an interrupted response, query
that UUID. A record marked `unknown` is an unresolved outcome, not permission
to take another exposure. Query returns the recorded status unchanged; list
includes only completed captures. Capabilities/status/list/asset/export do not
acquire a control lease or submit a capture. JSON carries metadata, not raw pixels.

Export uses the existing pinned verified-file path, synchronizes a sibling
temporary file and publishes to a new name. The destination parent must already
exist. Existing files are never replaced, a bad/missing source does not create
an output, and the source DNG is unchanged. This is local filesystem behavior,
not an arbitrary card/power-loss guarantee.

`tests/test_camera_offline.py` exercises real CLI processes and reference/native
authorities through capture, restarts, paginated listing, metadata and byte-exact
export. An actual import blocker makes the DaOS package unavailable during
basic photography. Foreign destination contents and source timestamps/hashes
are preserved; corrupt sources fail visibly. Together with the original two
CLI checks, seven Windows tests passed with both authorities available.

`tests/ui/offline_smoke.py` adds a separate actual-CLI session to the generic
ARM64 image test. First phase creates one basic capture and a two-step native
workflow; resume reopens the saved identities and completes without taking
another photograph. The helper records the real Python architecture and
preserves the original UUID/job/file hashes. Its results belong to the R8 target
evidence, not the older R7 image snapshot.
