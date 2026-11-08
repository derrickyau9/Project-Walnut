"""Single-process workflow manager sharing the existing CameraService instance."""
from __future__ import annotations
import json
import re
from pathlib import Path
import threading

from .bracket import BracketRunner, WorkflowError, WorkflowBusy


class WorkflowManager:
    def __init__(self, service, journal_path):
        self.runner = BracketRunner(service, Path(journal_path))
        self.lock = threading.RLock()
        self.threads = {}
        # Only an unowned journal can be called interrupted. A second manager
        # may inspect/control the live owner without rewriting its checkpoints.
        try:
            with self.runner.execution(), self.runner._db() as db:
                inconsistent = db.execute("SELECT job_id,status FROM jobs WHERE status IN ('completed','failed','cancelled') AND EXISTS (SELECT 1 FROM steps WHERE steps.job_id=jobs.job_id AND steps.status NOT IN ('completed','failed','cancelled'))").fetchall()
                for job_id, status in inconsistent:
                    db.execute("UPDATE jobs SET status='unknown',error=? WHERE job_id=?", ('saved terminal label has an unresolved request; reconcile the original ID', job_id))
                    db.execute("INSERT INTO controls VALUES(?,'cancel') ON CONFLICT(job_id) DO UPDATE SET desired='cancel'", (job_id,))
                    db.execute("INSERT INTO events(job_id,event,detail) VALUES(?,?,?)", (job_id, 'terminal_reservation_recovered', json.dumps({'previous_status':status})))
                interrupted = [r[0] for r in db.execute("SELECT job_id FROM jobs WHERE status IN ('running','pending')")]
                for job_id in interrupted:
                    db.execute("UPDATE jobs SET status='paused', error=? WHERE job_id=?", ('process interrupted; resume to reconcile', job_id))
                    db.execute("INSERT INTO controls VALUES(?, 'pause') ON CONFLICT(job_id) DO UPDATE SET desired='pause'", (job_id,))
                    db.execute("INSERT INTO events(job_id,event,detail) VALUES(?,?,?)", (job_id, 'interrupted', json.dumps({'reason':'manager restarted'})))
        except WorkflowBusy:
            pass

    def list_jobs(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise WorkflowError('job list limit must be an integer from 1 through 100')
        with self.runner._db() as db:
            rows = db.execute('SELECT job_id,status,error FROM jobs ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
        return [{'job_id':j, 'status':s, 'error':e} for j,s,e in rows]

    def inspect(self, job_id):
        result = self.runner.inspect(job_id)
        result['desired'] = self.runner.desired(job_id)
        return result

    def _active(self):
        return any(thread.is_alive() for thread in self.threads.values())

    def _launch(self, job_id, plan, desired='run', prepare=False):
        if self._active():
            raise WorkflowError('another workflow is active on this simulator')
        ownership = self.runner.execution()
        ownership.__enter__()
        launched = False
        def work():
            try:
                self.runner._run(job_id, plan)
            except Exception as exc:
                # Record unexpected manager failures so they are visible.
                self.runner._set_job(job_id, 'unknown', str(exc))
                self.runner._event(job_id, 'manager_error', {'error':str(exc)})
            finally:
                ownership.__exit__(None, None, None)
        try:
            if prepare:
                self.runner.prepare(job_id, plan)
                self.runner.recover_terminal_reservation(job_id)
                current = self.inspect(job_id)
                if current['status'] in {'completed','failed','cancelled'}: return current
                plan = current['plan']
            self.runner.set_desired(job_id, desired)
            try:
                thread = threading.Thread(target=work, name='workflow-' + job_id, daemon=True)
                self.threads[job_id] = thread
                thread.start()
                launched = True
            except Exception as exc:
                self.threads.pop(job_id, None)
                unresolved = any(step['status'] not in {'completed','failed','cancelled'} for step in self.runner.inspect(job_id)['steps'])
                self.runner._set_job(job_id, 'unknown' if unresolved else 'failed', 'workflow worker could not start: ' + str(exc))
                self.runner._event(job_id, 'worker_start_failed', {'error':str(exc)})
                raise WorkflowError('workflow worker could not start: ' + str(exc)) from exc
        finally:
            if not launched: ownership.__exit__(None, None, None)
        return self.inspect(job_id)

    def start(self, job_id, plan):
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", job_id):
            raise WorkflowError('job_id must contain only letters, digits, dot, underscore or hyphen')
        with self.lock:
            if self._active():
                raise WorkflowError('another workflow is active on this simulator')
            return self._launch(job_id, plan, prepare=True)

    def pause(self, job_id):
        with self.lock:
            job = self.inspect(job_id)
            if job['status'] == 'paused': return job
            if job['status'] not in {'pending', 'running'}:
                raise WorkflowError('only an active workflow can be paused')
            self.runner.set_desired(job_id, 'pause')
            return self.inspect(job_id)

    def resume(self, job_id):
        with self.lock:
            job = self.inspect(job_id)
            if job['status'] not in {'paused', 'unknown'}:
                raise WorkflowError('only a paused or unknown workflow can be resumed')
            return self._launch(job_id, job['plan'])

    def cancel(self, job_id):
        with self.lock:
            job = self.inspect(job_id)
            if job['status'] == 'cancelled': return job
            if job['status'] in {'completed', 'failed'}:
                raise WorkflowError('workflow is terminal; inspect it first')
            if job['status'] in {'paused', 'unknown'} and not self._active():
                return self._launch(job_id, job['plan'], desired='cancel')
            self.runner.set_desired(job_id, 'cancel')
            return self.inspect(job_id)
