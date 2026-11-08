"""Offline, journaled exposure bracketing over a camera service interface.

This module contains no sensor code and makes no exactly-once guarantee. A
submission whose result cannot be established is reported as unknown and is
never replaced by a new request automatically.
"""
from __future__ import annotations

import json
import math
import os
import stat
from contextlib import contextmanager
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

TERMINAL = {"complete", "failed", "cancelled", "unknown"}


class WorkflowError(Exception):
    pass


class WorkflowBusy(WorkflowError):
    """Another local process or runner owns execution of this journal."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def normalize_plan(plan: dict, *, strict: bool = False) -> dict:
    """Validate one plan without storage or camera access; return an independent copy.

    Legacy callers retain their accepted extra-field behavior. Portable recipes
    opt into the exact field set so credentials/results/paths cannot hitchhike.
    """
    if not isinstance(plan, dict):
        raise WorkflowError("plan must be an object")
    allowed = {"kind", "exposures_us", "sensor_mode", "requested_analogue_gain", "output_format", "interval_ms"}
    if strict and set(plan) - allowed:
        raise WorkflowError("plan has unsupported fields")
    kind = plan.get("kind", "bracket")
    if not isinstance(kind, str) or kind not in {"bracket", "interval", "dark", "flat"}:
        raise WorkflowError("unsupported workflow kind")
    if strict and kind != "interval" and "interval_ms" in plan:
        raise WorkflowError("interval_ms is only valid for an interval plan")
    exposures = plan.get("exposures_us")
    if (not isinstance(exposures, list) or not exposures or len(exposures) > 100
            or any(type(value) is not int or not 100 <= value <= 30000000 for value in exposures)):
        raise WorkflowError("exposures_us must contain 1 to 100 integers from 100 to 30000000")
    normalized = {"exposures_us": list(exposures), "sensor_mode": plan.get("sensor_mode", "sim-small"),
                  "requested_analogue_gain": plan.get("requested_analogue_gain", 1.0),
                  "output_format": plan.get("output_format", "dng")}
    mode = normalized["sensor_mode"]
    if not isinstance(mode, str) or mode not in {"sim-small", "sim-full"} or normalized["output_format"] != "dng":
        raise WorkflowError("unsupported simulator mode or output format")
    gain = normalized["requested_analogue_gain"]
    if (isinstance(gain, bool) or not isinstance(gain, (int, float))
            or not 1 <= gain <= 16 or not math.isfinite(gain)):
        raise WorkflowError("requested_analogue_gain must be between 1 and 16")
    if kind != "bracket":
        normalized["kind"] = kind
    if kind == "interval":
        interval = plan.get("interval_ms")
        if type(interval) is not int or not 0 <= interval <= 86400000:
            raise WorkflowError("interval_ms must be an integer from 0 to 86400000")
        normalized["interval_ms"] = interval
    return normalized


class BracketRunner:
    """Execute a bracket with persistent checkpoints and per-step request IDs.

    One caller must coordinate access to a job ID. `service` supplies acquire,
    release, get_state, configure, capture, query_capture and cancel_capture.
    A cancel event is cooperative at workflow boundaries, and requests camera
    cancellation during capture. A remote process kill still relies on the
    service's own recovery and the persisted request journal.
    """
    def __init__(self, service: Any, journal: str | Path, *, poll_interval: float = .05,
                 capture_timeout: float = 30.0):
        self.service = service
        self.journal = str(Path(journal).resolve())
        self.poll_interval = poll_interval
        self.capture_timeout = capture_timeout
        Path(self.journal).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, plan TEXT NOT NULL,
                    original TEXT, status TEXT NOT NULL, error TEXT);
                CREATE TABLE IF NOT EXISTS steps (
                    job_id TEXT NOT NULL, step INTEGER NOT NULL,
                    request_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
                    result TEXT, PRIMARY KEY(job_id, step));
                CREATE TABLE IF NOT EXISTS controls (
                    job_id TEXT PRIMARY KEY, desired TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL);
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.journal, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def _event(self, job_id: str, event: str, detail: Any = None) -> None:
        with self._db() as db:
            db.execute("INSERT INTO events(job_id,event,detail) VALUES(?,?,?)",
                       (job_id, event, _canonical(detail)))

    @contextmanager
    def execution(self):
        """Own this journal until completion; the OS releases ownership on death.

        This is local filesystem coordination, not a distributed lease. Resolve
        the journal first so symlink aliases use the same sidecar lock.
        """
        path = Path(self.journal + '.execution.lock')
        if path.is_symlink(): raise WorkflowError('workflow execution lock must be a regular file')
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, 'r+b') as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise WorkflowError('workflow execution lock must be a regular file')
            try:
                # Do not read an already locked byte: Windows denies that read
                # before LK_NBLCK can report the intended ownership conflict.
                if os.fstat(lock.fileno()).st_size == 0:
                    lock.write(b'0'); lock.flush()
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise WorkflowBusy('another workflow runner owns this journal') from exc
            try: yield
            finally:
                lock.seek(0)
                if os.name == 'nt': msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _job(self, job_id: str):
        with self._db() as db:
            return db.execute("SELECT plan,original,status,error FROM jobs WHERE job_id=?", (job_id,)).fetchone()

    def _step(self, job_id: str, n: int):
        with self._db() as db:
            return db.execute("SELECT request_id,status,result FROM steps WHERE job_id=? AND step=?", (job_id, n)).fetchone()

    def _set_step(self, job_id: str, n: int, request_id: str, status: str, result: Any = None):
        with self._db() as db:
            db.execute("INSERT INTO steps VALUES(?,?,?,?,?) ON CONFLICT(job_id,step) DO UPDATE SET status=excluded.status,result=excluded.result",
                       (job_id, n, request_id, status, None if result is None else _canonical(result)))

    def _reserve(self, job_id: str, n: int, request_id: str) -> bool:
        # An insert, not an upsert: concurrent invocations cannot both submit.
        try:
            with self._db() as db:
                db.execute("INSERT INTO steps VALUES(?,?,?,?,NULL)",
                           (job_id, n, request_id, "submitting"))
            return True
        except sqlite3.IntegrityError:
            return False

    def _set_job(self, job_id: str, status: str, error: str | None = None):
        with self._db() as db:
            db.execute("UPDATE jobs SET status=?,error=? WHERE job_id=?", (status, error, job_id))

    def recover_terminal_reservation(self, job_id: str):
        """Repair an old terminal label that did not settle its reserved ID.

        Caller owns execution(). Keep a cancel intent so this repair cannot
        itself authorize another capture; an explicit resume may change it.
        """
        with self._db() as db:
            row = db.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            pending = db.execute("SELECT 1 FROM steps WHERE job_id=? AND status NOT IN ('completed','failed','cancelled') LIMIT 1", (job_id,)).fetchone()
            if row and row[0] in {'completed','failed','cancelled'} and pending:
                error = 'saved terminal label has an unresolved request; reconcile the original ID'
                db.execute("UPDATE jobs SET status='unknown',error=? WHERE job_id=?", (error, job_id))
                db.execute("INSERT INTO controls VALUES(?,'cancel') ON CONFLICT(job_id) DO UPDATE SET desired='cancel'", (job_id,))
                db.execute("INSERT INTO events(job_id,event,detail) VALUES(?,?,?)",
                           (job_id, 'terminal_reservation_recovered', _canonical({'previous_status':row[0]})))

    def inspect(self, job_id: str) -> dict:
        row = self._job(job_id)
        if row is None:
            raise WorkflowError("unknown workflow ID")
        with self._db() as db:
            steps = db.execute("SELECT step,request_id,status,result FROM steps WHERE job_id=? ORDER BY step", (job_id,)).fetchall()
            events = db.execute("SELECT sequence,event,detail FROM events WHERE job_id=? ORDER BY sequence", (job_id,)).fetchall()
        return {"job_id": job_id, "plan": json.loads(row[0]), "status": row[2], "error": row[3],
                "steps": [{"step": n, "request_id": rid, "status": state,
                           "result": json.loads(result) if result else None} for n, rid, state, result in steps],
                "events": [{"sequence": seq, "event": event, "detail": json.loads(detail)} for seq, event, detail in events]}

    def prepare(self, job_id: str, plan: dict) -> dict:
        if not isinstance(job_id, str) or not job_id or len(job_id) > 100:
            raise WorkflowError("job_id must be a non-empty string of at most 100 characters")
        normalized = normalize_plan(plan)
        plan_json = _canonical(normalized)
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO jobs(job_id,plan,status) VALUES(?,?,?)", (job_id, plan_json, "pending"))
            db.execute("INSERT OR IGNORE INTO controls(job_id,desired) VALUES(?,?)", (job_id, "run"))
        job = self._job(job_id)
        if job[0] != plan_json:
            raise WorkflowError("workflow ID already belongs to a different plan")
        return normalized

    def desired(self, job_id: str) -> str:
        with self._db() as db:
            row = db.execute("SELECT desired FROM controls WHERE job_id=?", (job_id,)).fetchone()
        return row[0] if row else "run"

    def set_desired(self, job_id: str, desired: str) -> None:
        if desired not in {"run", "pause", "cancel"} or self._job(job_id) is None:
            raise WorkflowError("invalid workflow control")
        with self._db() as db:
            db.execute("INSERT INTO controls VALUES(?,?) ON CONFLICT(job_id) DO UPDATE SET desired=excluded.desired", (job_id, desired))
            db.execute("INSERT INTO events(job_id,event,detail) VALUES(?,?,?)", (job_id, "control_requested", _canonical({"desired": desired})))

    def run(self, job_id: str, plan: dict, cancel: threading.Event | None = None) -> dict:
        with self.execution():
            return self._run(job_id, plan, cancel)

    def _run(self, job_id: str, plan: dict, cancel: threading.Event | None = None) -> dict:
        """Run while the caller owns execution(); manager holds it across launch."""
        cancel = cancel or threading.Event()
        normalized = self.prepare(job_id, plan)
        self.recover_terminal_reservation(job_id)
        exposures = normalized["exposures_us"]
        job = self._job(job_id)
        if job[2] in {"completed", "failed", "cancelled"}:
            return self.inspect(job_id)
        lease = None
        original = json.loads(job[1]) if job[1] else None
        outcome, error = "completed", None
        with self._db() as db:
            pending = db.execute("SELECT step,request_id FROM steps WHERE job_id=? AND status NOT IN ('completed','failed','cancelled') ORDER BY step LIMIT 1", (job_id,)).fetchone()
        unresolved = pending
        heartbeat_at = 0.0
        heartbeat_interval = 5.0
        wait_interval = self.poll_interval
        def heartbeat(force=False):
            nonlocal heartbeat_at
            if lease is not None and hasattr(self.service, "heartbeat") and (force or time.monotonic() >= heartbeat_at):
                self.service.heartbeat(lease["token"], lease["epoch"])
                heartbeat_at = time.monotonic() + heartbeat_interval
        def desired():
            return "cancel" if cancel.is_set() else self.desired(job_id)
        try:
            if desired() in {"pause", "cancel"} and pending is None:
                outcome = "paused" if desired() == "pause" else "cancelled"
                return self._finish(job_id, outcome, None)
            lease = self.service.acquire("workflow:" + job_id)
            token, epoch = lease["token"], lease["epoch"]
            lease_seconds = lease.get("lease_seconds", 30)
            heartbeat_interval = min(5.0, lease_seconds / 3)
            wait_interval = min(self.poll_interval, heartbeat_interval)
            self._event(job_id, "control_acquired", {"epoch": epoch})
            if original is None:
                state = self.service.get_state()
                original = state.get("settings", state.get("configuration"))
                if not isinstance(original, dict):
                    raise WorkflowError("camera did not expose restorable settings")
                with self._db() as db:
                    db.execute("UPDATE jobs SET original=? WHERE job_id=?", (_canonical(original), job_id))
            self._set_job(job_id, "running")
            for n, exposure in enumerate(exposures):
                heartbeat()
                action = desired()
                previous = self._step(job_id, n)
                if previous and previous[1] == "completed":
                    continue
                if previous and previous[1] in {"failed", "cancelled"}:
                    outcome = previous[1]
                    break
                # A saved reservation may have reached the camera before a
                # process died. Settle that exact ID even under cancel intent;
                # controls can prevent a new shot but cannot erase an old one.
                if action in {"pause", "cancel"} and not previous:
                    outcome = "paused" if action == "pause" else "cancelled"
                    break
                if not previous and n > 0 and normalized.get("kind") == "interval":
                    prior = self._step(job_id, n - 1)
                    prior_record = json.loads(prior[2]) if prior and prior[2] else {}
                    target = prior_record.get("workflow_completed_unix", time.time()) + normalized["interval_ms"] / 1000.0
                    while time.time() < target:
                        heartbeat()
                        action = desired()
                        if action in {"pause", "cancel"}:
                            outcome = "paused" if action == "pause" else "cancelled"
                            break
                        time.sleep(min(wait_interval, max(0, target - time.time())))
                    if outcome != "completed":
                        break
                request_id = previous[0] if previous else str(uuid.uuid4())
                if previous:
                    unresolved = (n, request_id)
                    # An earlier process might have died immediately before or after
                    # submission. Only query that same ID; absence is ambiguous.
                    record = self.service.query_capture(request_id)
                    if record is None:
                        self._set_step(job_id, n, request_id, "unknown")
                        outcome, error = "unknown", "previous submission cannot be reconciled"
                        break
                else:
                    settings = {"sensor_mode": normalized["sensor_mode"], "requested_exposure_us": exposure,
                                "requested_analogue_gain": normalized["requested_analogue_gain"], "output_format": normalized["output_format"],
                                "simulation_scene": normalized.get("kind") if normalized.get("kind") in {"dark", "flat"} else "scene"}
                    heartbeat(force=True)
                    applied = self.service.configure(token, epoch, settings)
                    self._event(job_id, "configured", {"step": n, "requested": settings, "accepted": applied})
                    action = desired()
                    if action in {"pause", "cancel"}:
                        outcome = "paused" if action == "pause" else "cancelled"
                        break
                    request = dict(settings, request_id=request_id, token=token, control_epoch=epoch)
                    # Commit before submission. A crash between commit and submission is
                    # intentionally ambiguous rather than risking a second exposure.
                    if not self._reserve(job_id, n, request_id):
                        outcome, error = "unknown", "concurrent workflow invocation; reconcile the original request"
                        break
                    unresolved = (n, request_id)
                    self._event(job_id, "capture_submitting", {"step": n, "request_id": request_id})
                    try:
                        # SQLite checkpoint/event writes may have consumed most of the lease.
                        heartbeat(force=True)
                        record = self.service.capture(request)
                    except Exception as exc:
                        record = self.service.query_capture(request_id)
                        if record is None:
                            self._set_step(job_id, n, request_id, "unknown")
                            outcome, error = "unknown", f"capture outcome cannot be established: {exc}"
                            break
                end = time.monotonic() + self.capture_timeout
                cancel_sent = False
                def confirmed_record(value):
                    if value is not None and (not isinstance(value, dict) or value.get('request_id') != request_id
                                               or not isinstance(value.get('status'), str) or not value['status']):
                        raise WorkflowError('capture response does not identify the reserved request')
                    return value
                record = confirmed_record(record)
                while record is not None and record.get("status") not in TERMINAL:
                    heartbeat()
                    if desired() == "cancel" and not cancel_sent:
                        self.service.cancel_capture(token, epoch, request_id)
                        cancel_sent = True
                        self._event(job_id, "cancel_requested", {"step": n, "request_id": request_id})
                    if time.monotonic() >= end:
                        record = None
                        break
                    time.sleep(wait_interval)
                    record = confirmed_record(self.service.query_capture(request_id))
                if record is None:
                    self._set_step(job_id, n, request_id, "unknown")
                    outcome, error = "unknown", "capture status unavailable or timed out"
                    break
                status = record["status"]
                if status == "complete" and "workflow_completed_unix" not in record:
                    record = dict(record, workflow_completed_unix=time.time())
                unresolved = None
                if status != "complete":
                    self._set_step(job_id, n, request_id, status, record)
                    self._event(job_id, "capture_terminal", {"step": n, "record": record})
                    outcome, error = status, f"capture ended with status {status}"
                    break
                def confirmed_positive(value):
                    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
                if not confirmed_positive(record.get("actual_exposure_us")) or not confirmed_positive(record.get("actual_analogue_gain")) or not isinstance(record.get("file_path"), str) or not record["file_path"]:
                    outcome, error = "failed", "capture completed without confirmed actual exposure, gain and file path"
                    self._set_step(job_id, n, request_id, "failed", record)
                    self._event(job_id, "checkpoint_failed", {"step": n, "reason": error})
                    break
                # Commit completion only after checking the evidence. On a crash
                # before this point, resume reconciles the same request ID.
                self._set_step(job_id, n, request_id, "completed", record)
                self._event(job_id, "capture_terminal", {"step": n, "record": record})
                # The terminal journal record may become visible just before the
                # backend clears its active flag. Wait for idle, bounded by timeout.
                idle_end = time.monotonic() + self.capture_timeout
                while self.service.get_state().get("camera_state") != "idle":
                    heartbeat()
                    if time.monotonic() >= idle_end:
                        raise WorkflowError("camera did not return to idle")
                    time.sleep(wait_interval)
        except Exception as exc:
            if unresolved is not None:
                step_number, rid = unresolved
                self._set_step(job_id, step_number, rid, "unknown")
                outcome, error = "unknown", "capture outcome cannot be established: " + str(exc)
            else:
                outcome, error = "failed", str(exc)
            self._event(job_id, "workflow_error", {"error": error})
        finally:
            if lease is not None:
                # A camera owns its own state machine; restoration failure is explicit.
                if original is not None:
                    try:
                        heartbeat(force=True)
                        self.service.configure(lease["token"], lease["epoch"], original)
                        # A clean pause releases ownership. A later owner may
                        # legitimately change settings before this job resumes;
                        # its next lease must snapshot that then-current state.
                        # Keep the historical snapshot in the event, atomically
                        # clear only the outstanding restoration checkpoint.
                        with self._db() as db:
                            db.execute("UPDATE jobs SET original=NULL WHERE job_id=?", (job_id,))
                            db.execute("INSERT INTO events(job_id,event,detail) VALUES(?,?,?)",
                                       (job_id, "settings_restored", _canonical(original)))
                    except Exception as exc:
                        self._event(job_id, "restore_failed", {"error": str(exc)})
                        detail = "settings restoration failed: " + str(exc)
                        error = (error + "; " if error else "") + detail
                        if outcome == "completed":
                            outcome = "failed"
                try:
                    heartbeat(force=True)
                    self.service.release(lease["token"], lease["epoch"])
                    self._event(job_id, "control_released")
                except Exception as exc:
                    self._event(job_id, "release_failed", {"error": str(exc)})
                    detail = "control release failed: " + str(exc)
                    error = (error + "; " if error else "") + detail
                    if outcome == "completed":
                        outcome = "failed"
        # Cancel can arrive while a pending pause is restoring settings.
        if outcome == "paused" and desired() == "cancel":
            outcome = "cancelled"
        return self._finish(job_id, outcome, error)

    def _finish(self, job_id: str, outcome: str, error: str | None):
        self._set_job(job_id, outcome, error)
        self._event(job_id, "workflow_finished", {"status": outcome, "error": error})
        return self.inspect(job_id)
