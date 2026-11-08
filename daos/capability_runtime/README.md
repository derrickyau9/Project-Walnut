# Offline exposure bracket workflow

`BracketRunner` is an independently written, synchronous Python workflow runner
for the compatible reference service and native authority client. Managed
workflows are exposed by the local HTTP console through one existing camera
authority. All supported scenes and captures remain synthetic. Run the workflow
and portable-recipe integration tests with:

```powershell
python -B -m unittest tests.test_workflow tests.test_workflow_recipes -v
```

Example embedded in an application that owns its `CameraService` instance:

```python
from camera.camerad.service import CameraService
from daos.capability_runtime.bracket import BracketRunner

service = CameraService("./var/simulator")
try:
    runner = BracketRunner(service, "./var/workflows.sqlite3")
    result = runner.run("example-1", {"exposures_us": [5000, 10000, 20000]})
    print(result["status"], result["steps"])
finally:
    service.close()
```

Use a new job ID for a new bracket. Reopening the same completed job returns
its recorded result; reusing an ID with a different plan is rejected. If a
process dies around submission, rerun with the same job ID: the runner queries
the recorded request ID and does not issue another exposure if it is absent or
unconfirmed. `unknown` explicitly requires investigation. Each journaled event
is available through `inspect(job_id)`. Keep workflow and camera journals on
persistent storage. A per-journal OS file lock allows one local runner at a time,
including direct runners, managers and CLI processes. A competing execution is
rejected before changing job controls or submitting a capture. Opening another
manager can inspect or signal the current owner; it does not mark that live job
interrupted. The OS releases the execution lock after process death. This is
local filesystem coordination, not a distributed workflow lease. Keep the
sidecar execution lock with the journal and use a filesystem that implements
the platform's local file locking. Cancel via a `threading.Event` passed to
`run`; cancellation is requested at the backend and completion is observed. The
settings observed for each lease are restored and ownership released on a best-effort basis;
failures appear in the event log. Use a dedicated authority directory; a second reference/native authority cannot
open the same active camera journal. Managed HTTP clients share the server-owned
authority rather than creating competing journal processes.

## Command line

The command below creates a dedicated simulator under `var/hf-workflow` and
prints the full job record as JSON. Generated files and the simulator journal
are in `var/hf-workflow/simulator/`, and workflow events are in
`var/hf-workflow/workflow.sqlite3`.

```powershell
python -m daos.capability_runtime run --job bracket-001 --exposures-us 5000 10000 20000
python -m daos.capability_runtime inspect --job bracket-001
```

The CLI is offline and simulator-only. `--data-dir PATH` placed before the
subcommand chooses another dedicated directory. `sim-small` is the default;
`--mode sim-full` generates full-size synthetic images and needs substantial
memory and disk space. Ctrl+C requests cancellation and waits for a terminal
status. A file lock prevents two CLI runs sharing the same data directory.
The HTTP server uses its own default directory `var/hf-sim`; do not point any
other service or process at the CLI's `simulator/` subdirectory while it runs.
Inspect returns the saved record without starting a camera. Exit status is 0
when complete, 2 for an unknown result, 130 for cancellation, and 1 on error.

Continue a saved plan without resupplying its recipe or parameters:

```powershell
python -m daos.capability_runtime --data-dir var/hf-workflow list
python -m daos.capability_runtime --data-dir var/hf-workflow resume --job bracket-001
python -m daos.capability_runtime --data-dir var/hf-workflow pause --job bracket-001
python -m daos.capability_runtime --data-dir var/hf-workflow cancel --job bracket-001
```

`resume` and an explicit `run` deliberately clear a saved pause intent under
execution ownership. Both retain completed step IDs and reconcile any reserved
request before creating another step. `inspect`, `list` and `pause` start no
camera. An empty `list` creates no data directory. A terminal saved result can
be returned without opening an authority. Select the same authority options as
the original run when continuation needs camera access.

Pause is cooperative at workflow boundaries. A separate CLI `cancel` command
can persist cancellation intent while an existing runner owns the journal; that
command returns exit 0 to acknowledge the intent and includes `desired=cancel`
in its JSON. Inspect the saved status for its eventual result. If no runner is
active, `cancel` reconciles the saved request using the selected authority and
returns the settled status (130 when cancelled, 2 when unknown). A complete
capture remains complete even if cancellation arrived too late; cancellation
suppresses remaining unsubmitted shots and cannot undo an exposure.

An unresolved request is never hidden by a successful cancellation label. The
runner queries the original ID, requests backend cancellation if it is still
active, and observes a terminal result before restoring settings. Missing,
unqueryable or mismatched request evidence stays `unknown`. Repeated cancel or
resume attempts use that ID and never replace it. Older terminal workflow rows
that still contain an unresolved reservation are reopened as `unknown` under
execution ownership, with cancellation intent, so they can be investigated and
explicitly reconciled. Merely inspecting the journal does not perform this repair.
After a clean settings restoration the outstanding snapshot is cleared
atomically with its event; a later lease snapshots the then-current settings,
preserving changes made by another controller while the workflow was paused.

## Portable offline recipes

A recipe is a bounded JSON artifact containing only a display name and a saved
plan. Exporting one opens no camera and writes no workflow/capture journal:

```powershell
python -m daos.capability_runtime recipe-export --name "Three exposure bracket" --output bracket.recipe.json --exposures-us 5000 10000 20000
python -m daos.capability_runtime --data-dir var/recipe-session run --job bracket-001 --recipe bracket.recipe.json
```

Use an explicit native authority when required:

```powershell
python -m daos.capability_runtime --data-dir var/native-recipe-session --authority native --native-daemon build/native/Release/hf-camerad.exe run --job bracket-001 --recipe bracket.recipe.json
```

`--recipe` is mutually exclusive with `--exposures-us` and rejects explicit gain,
mode, kind or interval overrides. The imported plan is therefore the plan that
runs. The existing explicit-parameter commands remain available. Reuse the same
job ID and unchanged plan to reconcile interrupted work; choose a deliberately
new job ID for a new execution. A recipe carries no job/request IDs, results,
control tokens, owner details or output paths, so sharing it cannot replay a job
or disclose a stored photo location. Sharing means copying the file through the
user's chosen channel; this tool sends nothing to another person or service.

The versioned format is exactly:

```json
{
  "format": "hasslefree.workflow-recipe.v1",
  "name": "Three exposure bracket",
  "plan": {
    "exposures_us": [5000, 10000, 20000],
    "sensor_mode": "sim-small",
    "requested_analogue_gain": 1.0,
    "output_format": "dng"
  }
}
```

The normalized bracket plan omits `kind`. An interval plan adds
`"kind": "interval"` plus required `interval_ms` from 0 through 86400000; dark
and flat plans add their respective `kind`. `interval_ms` is rejected on other
kinds. Defaults are `sim-small`, gain 1.0 and DNG. Exposures contain 1-100 integral
JSON numbers from 100 through 30000000 microseconds; gain is finite from 1 through
16. Integral decimal/exponent JSON numbers normalize to integers in recipes,
matching browser number semantics. The ordinary legacy workflow API keeps its
existing exact-integer validation. Name length is 1-80 trimmed printable Unicode
characters.

Import accepts at most 64 KiB of ordinary UTF-8 JSON. Duplicate keys, nonfinite
numbers, unsupported versions and unknown fields at either recipe or plan level
are rejected before any camera is opened. The pure `normalize_plan` function is
shared by recipe validation and `BracketRunner.prepare`, with strict field
validation enabled for recipes. It does not create storage or access a camera.

`recipe-export` creates a new file and never overwrites an existing entry. The
module validates before creating directories, writes and synchronizes a sibling
temporary file, then publishes it with a create-only hard link. The destination
filesystem must support that operation; an unsupported save is an error, not a
partially successful recipe. No arbitrary power-loss durability is asserted.
The CLI prints portable JSON after a successful export. Python applications can
use `normalize_recipe`, `loads_recipe`, `load_recipe`, `dumps_recipe` and
`save_recipe` from `daos.capability_runtime.recipes` without a running authority.

The tests exercise all four kinds, strict malformed/private-field rejection,
concurrent create-only export, actual offline reference/native execution and
same-job reconciliation without duplicate captures. Native execution uses a
copied built test executable to avoid holding the development build output open.
These are software fixture results; physical capture and target-body workflows
remain unverified.

## Managed workflows and simulator scenes

The local simulator server can use `WorkflowManager` with its existing camera
instance. It supports bracket plans, interval plans, and dark/flat synthetic
calibration sequences. For example:

```json
{"kind":"interval","exposures_us":[5000,5000,5000],"interval_ms":1000}
```

`kind` may be `bracket` (the default), `interval`, `dark`, or `flat`; each plan
contains one exposure in microseconds per shot. Interval timing is measured
from recorded completion of one shot to the next submission, using the system
clock. This is a minimum software delay, not a real-time cadence guarantee;
pauses, scheduling, clock changes and slow storage can lengthen it. Dark/flat
select simplified synthetic frames and cannot establish real sensor calibration
or optical conditions.

Pause and cancel requests are saved in the workflow journal. Pause takes effect
after any active capture reaches a terminal state, restores settings and
releases ownership; resume continues the same plan and uses the same request IDs
for any unresolved shots. Cancel asks the simulator to cancel an active capture
and prevents future shots. A process restart marks previously running jobs
paused, and resuming reconciles their recorded requests. An unconfirmed request
stays `unknown` and does not cause an automatic repeat. Only one managed
workflow runs against this camera at a time. The manager keeps its lease alive
while waiting; independent basic capture remains served by the camera service
when the workflow does not own control.
A `WorkflowManager` must be constructed only once per running service: its
startup recovery assumes no previous manager is still executing and marks
interrupted jobs paused. The server owns this lifecycle; do not instantiate a
second manager against the same journal during operation.
Managed HTTP job IDs must contain only letters, digits, dot, underscore or
hyphen. Leases are renewed before camera operations and during waits, but Python
and the host operating system cannot guarantee renewal under arbitrary stalls;
if control expires, the workflow reports the failure or an unknown outcome
rather than assuming that an exposure may safely be repeated.


Managed job browsing returns the newest 50 records by default, bounded to at
most 100 for direct `list_jobs(limit=...)` callers. This does not prune history;
older jobs remain inspectable by ID. The console uses this existing HTTP list
alongside validated portable recipe import/export and the same pause, resume
and cancel operations.
