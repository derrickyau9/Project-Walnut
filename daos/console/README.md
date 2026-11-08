# HF-Sim console

From the project root, run `python -m camera.camerad.server --data-dir var/hf-sim --port 8787`, then open `http://127.0.0.1:8787/`. This is a local simulator console, not a hardware camera or live view.

Acquire control before capturing. The console keeps the control token and epoch in this tab's session storage so that a reload in the same tab can retain control. While open, the page sends a heartbeat every 10 seconds to renew the simulator's 30-second control lease. It clears the saved token after release or when the service reports that the lease is invalid. Release control before closing the tab; if the tab is lost, the lease expires and another client can acquire control after the timeout. A suspended tab or prolonged connection loss may also let the lease expire, in which case acquire control again. Restarting the service invalidates the old token. Session storage is accessible to JavaScript on the local origin, so use this development server only on a trusted machine.

Each capture is submitted once. If a response is interrupted, query the retained request ID rather than resending it. A status of `unknown` means the service cannot establish whether the prior operation completed.

Start preview after acquiring control. The preview is an independently generated synthetic image, not a sensor feed or a full-resolution capture. Use **Apply settings to preview** to configure its exposure and gain; the capture form otherwise sends its own requested settings. The page polls at no more than 5 Hz and shows the frame ID and timestamp. It hides the frame when it initiates a capture or when a preview request fails; for a capture initiated by another client there can be up to a polling interval before the page observes that preview is paused. Release console control before starting a simulated workflow, which acquires its own lease. Sequence jobs can be queried by ID after an interrupted response.

The injected disconnect leaves the simulated device disconnected while the service remains available. Acquire control if necessary, then use **Reconnect simulated device**. This recovery button is part of HF-Sim and makes no claim about physical hardware.


## Retained photos

**Retained photos** lists 20 committed captures at a time. **Next older** replaces
the page; **Newest / refresh** returns to the latest entries. Selecting a row
renders an actual saved-DNG Bayer display conversion, limited to 320 x 240 and
256 KiB. It is synthetic and uncalibrated, and is separate from live preview.
Gallery reads require no control lease and never change the active capture ID.
Unknown/failed requests remain accessible through original-ID query.

**Download DNG** requests the selected canonical capture through the API. The
browser chooses its local download destination and overwrite/rename behavior;
no arbitrary destination path is sent to the server. The SDK and Qt UI also
provide explicit create-only local export. Downloading and reviewing do not
submit a capture or release another client's control.

## Portable workflows

Choose a recipe name and bracket, interval, dark or flat settings, then **Export
recipe** to download a versioned JSON file. **Import recipe** validates a file
before loading those settings; importing starts no job. **Start sequence**
creates a new job ID, while query/pause/resume/cancel keep using the selected
existing job. Release console control before starting a managed workflow.
**Refresh recent jobs** lists the newest 50 jobs for inspection. Older records
are retained and can still be queried by a known job ID.

Portable recipes contain only `format`, `name` and a validated plan. They exclude
tokens, job/request IDs, results and paths, reject duplicate/unknown fields and
non-finite numbers, and are bounded to 64 KiB. The browser and offline Python
CLI share the same normalization contract. Sharing means copying this file using
your chosen channel; the console sends no message or recipe to other people.
See the [offline recipe commands](../capability_runtime/README.md#portable-offline-recipes).

Pause waits for an active shot to reach a terminal result before the runner
releases control. Resume reconciles recorded request IDs rather than replaying
unconfirmed shots. Cancel prevents future shots and requests cancellation of an
active one; it does not delete retained files. Interval delays are software
minimums, not guaranteed camera timing. Dark/flat recipes select synthetic
scenes and provide no measured calibration.

The new browser module runs without Node. Optional Node contract tests and the
actual Edge/native-HTTP integration smoke can be run from the repository root:

```powershell
python -B -m unittest tests.test_console_contracts tests.test_workflow_recipes -v
python tests/ui/round6_smoke.py
```

The smoke uses an isolated temporary browser profile and copied native test
executable. It verifies retained thumbnail/download, portable recipe roundtrip,
recent-job inspection, pause/resume/cancel, and a 390-pixel viewport. It captures
no browser screenshots. Optional tools are skipped explicitly when unavailable;
the recorded round-6 run executed successfully with no skip.
