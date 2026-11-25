"""Background job queue (doc 06 §3, B5).

* Jobs live in the `jobs` table; enqueueing is idempotent (unique per owner, stage, version).
* N slot threads in the engine each own one worker process (worker.py). A slot claims a job
  with one UPDATE ... RETURNING, sends it to its worker, waits, and writes the result. Only
  the engine writes to the database, through this module's own connection and lock.
* Retry with backoff 30 s, 2 min, 10 min, 1 h; after `max_attempts` the job is `dead`.
  StageError (corrupt, unsupported) fails at once. A job whose worker process dies twice is
  `failed`: no retry loop on a file that crashes a native library.
* Startup: jobs left `running` by a crash or kill go back to `queued`.
* Pause/resume (persisted), resource settings (threads, RAM, only when idle / on AC) gate claiming.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import sqlite3
import threading
import time
from pathlib import Path

from . import db, handlers, power, settings
from .worker import StageFn, worker_main

log = logging.getLogger("ivsidecar.jobs")

BACKOFF_S = (30, 120, 600, 3600)
MAX_CRASHES = 2
JOB_TIMEOUT_S = 600  # a single file taking longer than this is treated like a crash
NOW = handlers.NOW


class Worker:
    """One worker process, started on first use and restarted after a crash."""

    def __init__(self, library_dir: Path, vips_cache_mb: int, stage_fn: StageFn | None):
        self._args = (str(library_dir), vips_cache_mb, stage_fn)
        self._proc: mp.process.BaseProcess | None = None
        self._pipe = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def _ensure(self) -> None:
        if self.alive:
            return
        self._discard()
        ctx = mp.get_context("spawn")  # same behaviour on Windows, Linux and the frozen build
        parent, child = ctx.Pipe()
        self._proc = ctx.Process(target=worker_main, args=(child, *self._args), daemon=True, name="iv-worker")
        self._proc.start()
        child.close()
        self._pipe = parent

    def run(self, msg: tuple, timeout_s: float, stop: threading.Event) -> tuple[str, object]:
        self._ensure()
        try:
            self._pipe.send(msg)
        except (BrokenPipeError, OSError) as e:
            self._discard()
            return "crash", f"worker unavailable: {e}"
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                if self._pipe.poll(0.25):
                    _job_id, outcome, data = self._pipe.recv()
                    return outcome, data
            except (EOFError, OSError):
                return self._crashed()
            if not self._proc.is_alive():
                return self._crashed()
            if stop.is_set():
                self._discard()
                return "interrupted", None
            if time.monotonic() > deadline:
                self._discard()
                return "crash", f"timed out after {timeout_s:.0f} s"

    def _crashed(self) -> tuple[str, str]:
        self._proc.join(2)
        code = self._proc.exitcode
        self._discard()
        return "crash", f"worker process exited (code {code})"

    def _discard(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            self._proc.kill()
            self._proc.join(2)
        if self._pipe is not None:
            self._pipe.close()
        self._proc, self._pipe = None, None

    def stop(self) -> None:
        if self.alive:
            try:
                self._pipe.send(None)
                self._proc.join(3)
            except (BrokenPipeError, OSError):
                pass
        self._discard()


class JobQueue:
    def __init__(
        self,
        library_dir: Path,
        stage_fn: StageFn | None = None,
        poll_s: float = 0.5,
        job_timeout_s: float = JOB_TIMEOUT_S,
        max_pixels: int | None = None,
    ):
        self.library_dir = library_dir
        self.conn = db.connect(library_dir / "library.db")
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._slots: list[threading.Thread] = []
        self._slots_lock = threading.Lock()
        self._busy = 0
        self._stage_fn = stage_fn
        self._poll_s = poll_s
        self._timeout_s = job_timeout_s
        self._max_pixels = max_pixels
        self._res_cache: tuple[float, settings.Resources] | None = None
        self.paused = settings.load(self.conn, "queue").paused
        self.gate: str | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        n = self.recover()
        if n:
            log.info("crash recovery: %d running job(s) re-queued", n)
        self._ensure_slots()

    def stop(self) -> None:
        self._stop.set()
        self.wake()
        for t in self._slots:
            t.join(10)
        with self._lock:
            self.conn.close()  # Windows keeps an open database file locked

    def recover(self) -> int:
        with self._lock:
            return self.conn.execute(
                "UPDATE jobs SET status = 'queued', started_at = NULL WHERE status = 'running'"
            ).rowcount

    def wake(self) -> None:
        """Re-read settings and look for work now (after an enqueue or a settings change)."""
        self._res_cache = None
        if not self._stop.is_set():
            self._ensure_slots()
        with self._cond:
            self._cond.notify_all()

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            settings.save(self.conn, "queue", settings.QueueState(paused=paused))
        self.paused = paused
        self.wake()

    def retry_failed(self) -> int:
        with self._lock:
            n = self.conn.execute(
                "UPDATE jobs SET status = 'queued', attempts = 0, crashes = 0, last_error = NULL, "
                f"run_after = {NOW} WHERE status IN ('failed', 'dead')"
            ).rowcount
        self.wake()
        return n

    def state(self) -> dict:
        return {"paused": self.paused, "gate": self.gate, "slots": self._threads(), "busy": self._busy}

    # ------------------------------------------------------------------ helpers
    def _resources(self) -> settings.Resources:
        now = time.monotonic()
        if self._res_cache is None or now - self._res_cache[0] > 2:
            with self._lock:
                self._res_cache = (now, settings.load(self.conn, "resources"))
        return self._res_cache[1]

    def _threads(self) -> int:
        return self._resources().threads

    def _ensure_slots(self) -> None:
        with self._slots_lock:
            self._add_slots()

    def _add_slots(self) -> None:
        while len(self._slots) < self._threads():
            idx = len(self._slots)
            t = threading.Thread(target=self._slot, args=(idx,), daemon=True, name=f"iv-slot-{idx}")
            self._slots.append(t)
            t.start()

    def _gate(self, idx: int) -> str | None:
        if self.paused:
            return "paused"
        res = self._resources()
        if idx >= res.threads:
            return "slot_off"
        reason = power.gate_reason(res.only_when_idle, res.only_on_ac)
        self.gate = reason
        return reason

    def _wait(self) -> None:
        with self._cond:
            self._cond.wait(self._poll_s)

    def _claim(self) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                f"UPDATE jobs SET status = 'running', started_at = {NOW} WHERE id = ("
                f"  SELECT id FROM jobs WHERE status = 'queued' AND run_after <= {NOW}"
                "  ORDER BY priority, run_after, id LIMIT 1) RETURNING *"
            ).fetchone()

    def _vips_cache_mb(self) -> int:
        res = self._resources()
        return max(64, min(1024, res.max_ram_mb // max(1, res.threads) // 2))

    # ------------------------------------------------------------------ slot loop
    def _slot(self, idx: int) -> None:
        worker = Worker(self.library_dir, self._vips_cache_mb(), self._stage_fn)
        try:
            while not self._stop.is_set():
                if self._gate(idx):
                    worker.stop()  # give the memory back while waiting
                    self._wait()
                    continue
                job = self._claim()
                if job is None:
                    self._wait()
                    continue
                try:
                    with self._lock:
                        payload = handlers.build_payload(self.conn, job, self._max_pixels)
                except handlers.MissingFile as e:
                    self._finish(job, "fatal", f"missing_file: {e}")
                    continue
                self._busy += 1
                try:
                    outcome, data = worker.run((job["id"], job["stage"], payload), self._timeout_s, self._stop)
                finally:
                    self._busy -= 1
                self._finish(job, outcome, data)
        except Exception:  # noqa: BLE001 - a slot must never die silently
            log.exception("job slot %d stopped", idx)
        finally:
            worker.stop()

    # ------------------------------------------------------------------ results
    def _finish(self, job: sqlite3.Row, outcome: str, data) -> None:
        try:
            self._commit(job, outcome, data)
        except Exception as e:  # noqa: BLE001 - e.g. a handler bug: keep the job, retry later
            log.exception("writing result of job %s (%s) failed", job["id"], job["stage"])
            self._commit(job, "retry", f"engine: {type(e).__name__}: {e}")
        if outcome == "ok":
            with self._cond:
                self._cond.notify_all()  # the next stage was just queued

    def _commit(self, job: sqlite3.Row, outcome: str, data) -> None:
        jid = job["id"]
        with self._lock:
            c = self.conn
            c.execute("BEGIN IMMEDIATE")
            try:
                if outcome == "ok":
                    status, note = handlers.apply_result(c, job, data)
                    c.execute(
                        f"UPDATE jobs SET status = ?, finished_at = {NOW}, last_error = ? WHERE id = ?",
                        (status, note, jid),
                    )
                elif outcome == "fatal":
                    self._give_up(c, job, "failed", str(data))
                elif outcome == "retry":
                    attempts = job["attempts"] + 1
                    if attempts >= job["max_attempts"]:
                        c.execute("UPDATE jobs SET attempts = ? WHERE id = ?", (attempts, jid))
                        self._give_up(c, job, "dead", str(data))
                    else:
                        delay = BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
                        c.execute(
                            "UPDATE jobs SET status = 'queued', attempts = ?, last_error = ?, started_at = NULL, "
                            "run_after = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?) WHERE id = ?",
                            (attempts, str(data), f"+{delay} seconds", jid),
                        )
                elif outcome == "crash":
                    crashes = job["crashes"] + 1
                    c.execute("UPDATE jobs SET crashes = ?, attempts = attempts + 1 WHERE id = ?", (crashes, jid))
                    if crashes >= MAX_CRASHES:
                        self._give_up(c, job, "failed", f"worker crashed {crashes} times: {data}")
                    else:
                        c.execute(
                            "UPDATE jobs SET status = 'queued', last_error = ?, started_at = NULL WHERE id = ?",
                            (str(data), jid),
                        )
                    log.warning("job %s (%s) crashed its worker: %s", jid, job["stage"], data)
                elif outcome == "interrupted":
                    c.execute("UPDATE jobs SET status = 'queued', started_at = NULL WHERE id = ?", (jid,))
                else:
                    raise ValueError(f"unknown outcome {outcome}")
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    @staticmethod
    def _give_up(c: sqlite3.Connection, job: sqlite3.Row, status: str, error: str) -> None:
        c.execute(
            f"UPDATE jobs SET status = ?, last_error = ?, finished_at = {NOW} WHERE id = ?",
            (status, error[:1000], job["id"]),
        )
        handlers.apply_failure(c, job, error)


def selftest(timeout_s: float = 60) -> dict:
    """Runs one generated JPEG through stages 1-4 in a real worker process, in a temporary
    library. Used by the Windows build to prove that spawned workers start in the frozen exe."""
    import tempfile

    from PIL import Image

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="iv-queue-selftest-", ignore_cleanup_errors=True) as tmp:
        lib, src = Path(tmp) / "lib", Path(tmp) / "src"
        src.mkdir()
        Image.new("RGB", (64, 48), (200, 80, 20)).save(src / "selftest.jpg", quality=90)
        conn = db.open_library(lib)
        settings.save(conn, "resources", settings.Resources(threads=1))
        conn.execute(
            "INSERT INTO sources (uid, kind, display_name, root_path) VALUES ('selftest', 'local_folder', 's', ?)",
            (str(src),),
        )
        handlers.register_file(conn, 1, "selftest.jpg", "selftest", 1, "t")
        q = JobQueue(lib)
        q.start()
        try:
            status = None
            while time.monotonic() - started < timeout_s:
                row = conn.execute("SELECT status, error FROM assets").fetchone()
                status = row[0] if row else None
                if status in ("ready", "failed"):
                    break
                time.sleep(0.1)
            jobs = dict(conn.execute("SELECT stage, coalesce(last_error, status) FROM jobs").fetchall())
        finally:
            q.stop()
            conn.close()
    return {"ok": status == "ready", "asset": status, "jobs": jobs, "ms": round((time.monotonic() - started) * 1000)}
