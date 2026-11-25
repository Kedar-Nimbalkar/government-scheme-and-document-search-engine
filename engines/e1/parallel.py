"""Supervise spawn workers and one dedicated E1 writer."""

import json
import logging
import multiprocessing as mp
import pickle
import time
from collections import deque
from dataclasses import dataclass, replace
from multiprocessing.managers import SyncManager
from pathlib import Path
from queue import Empty, Full, Queue

from .jobs import open_jobs
from .processes import Frame, Limits, POLL_SECONDS, RETRYABLE, Task, worker_main, writer_main
from .reference import FILE_TYPES, Source, parse_source, file_source, source_report

LOGGER = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    pass


class MeasuredQueue(Queue):
    def __init__(self, maxsize):
        super().__init__(maxsize)
        self.high_water = 0

    def _put(self, item):
        super()._put(item)
        self.high_water = max(self.high_water, self._qsize())

    def peak(self):
        with self.mutex:
            return self.high_water


class QueueManager(SyncManager):
    pass


QueueManager.register("MeasuredQueue", MeasuredQueue)


@dataclass
class Slot:
    process: object
    tasks: object
    task: Task | None = None
    deadline: float = 0
    writing: bool = False


def _log(event, **fields):
    LOGGER.info("%s", json.dumps({"event": event, **fields}, separators=(",", ":")))


def _stop(process):
    if process.is_alive():
        process.terminate()
    process.join(timeout=3)
    if process.is_alive():
        process.kill()
        process.join(timeout=3)


class Supervisor:
    def __init__(self, ctx, manager, writer, incoming, outgoing, results, limits, max_tokens, parser, jobs,
                 sources, database_mode, pdf_root):
        self.ctx, self.manager, self.writer = ctx, manager, writer
        self.incoming, self.outgoing, self.results = incoming, outgoing, results
        self.limits, self.max_tokens, self.parser, self.jobs = limits, max_tokens, parser, jobs
        self.slots = []
        self.active = {}
        self.worker_pids = []
        self.reports = []
        self.reconciled = []
        self.task_peak = 0
        self.counter = 0
        self.sources = deque((source, 1) for source in sources)
        self.database_mode, self.pdf_root = database_mode, pdf_root
        self.scheme_records = {source.doc_id: source for source in sources if source.source_type == "scheme_record"}

    def _start_worker(self):
        tasks = self.manager.MeasuredQueue(1)
        process = self.ctx.Process(target=worker_main, args=(tasks, self.results, self.limits, self.max_tokens, self.parser))
        process.start()
        self.worker_pids.append(process.pid)
        _log("worker_started", pid=process.pid)
        return Slot(process, tasks)

    def _check_writer(self):
        if not self.writer.is_alive():
            raise PipelineError(f"Writer exited unexpectedly (exit code {self.writer.exitcode})")

    def send(self, action, value):
        deadline = time.monotonic() + self.limits.timeout_seconds
        while time.monotonic() < deadline:
            self._check_writer()
            try:
                self.incoming.put((action, value), timeout=POLL_SECONDS)
                return
            except Full:
                continue
        raise PipelineError("Writer queue did not drain before timeout")

    def _ack(self, expected, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                kind, value = self.outgoing.get(timeout=POLL_SECONDS)
            except Empty:
                self._check_writer()
                continue
            if kind == "fatal":
                raise PipelineError(f"Writer failed ({value})")
            if kind == "reconciled" and expected == "ready":
                self.reconciled.append(value)
                _log("job_reconciled", **value)
                continue
            if kind != expected:
                raise PipelineError("Unexpected writer message")
            return value
        raise PipelineError("Writer acknowledgement timed out")

    def _record(self, report):
        self.reports.append(report)
        _log("job_outcome", **{key: report[key] for key in ("doc_id", "job_id", "status", "reason") if key in report})

    def _next(self):
        self.counter += 1
        if not self.database_mode:
            if not self.sources:
                return None
            source, attempt = self.sources.popleft()
            return Task(self.counter, source, attempt)
        while (row := self.jobs.claim()) is not None:
            job_id, doc_id, attempt, lease_token, _, relative, source_type = row
            try:
                if source_type in FILE_TYPES:
                    source = file_source({"source_type": source_type, "doc_id": str(doc_id),
                                         "job_id": str(job_id), "file_path": relative}, self.pdf_root)
                else:
                    source = replace(self.scheme_records[str(doc_id)], job_id=str(job_id))
            except (ValueError, OSError, RuntimeError, KeyError):
                reason = "INVALID_JOB_SOURCE" if source_type in FILE_TYPES else "MISSING_SCHEME_RECORD"
                outcome = self.jobs.fail(job_id, lease_token, False, reason)
                self._record({"doc_id": str(doc_id), "job_id": str(job_id), "status": outcome[1] if outcome else "LEASE_LOST",
                              "reason": reason})
                continue
            return Task(self.counter, source, attempt, str(lease_token))
        return None

    def _dispatch(self):
        for index, slot in enumerate(self.slots):
            if slot.task is not None:
                continue
            task = self._next()
            if task is None:
                break
            if not slot.process.is_alive():
                slot.process.join()
                self.task_peak = max(self.task_peak, slot.tasks.peak())
                slot = self.slots[index] = self._start_worker()
            slot.task = task
            slot.deadline = time.monotonic() + self.limits.timeout_seconds
            self.active[task.key] = slot
            # Remove non-ID payload from the writer's start message.
            self.send("start", replace(task, source=replace(task.source, text=None, pdf_path=None)))
            if len(pickle.dumps(task, protocol=pickle.HIGHEST_PROTOCOL)) > self.limits.task_bytes:
                self._cancel(slot, "TASK_LIMIT")
            else:
                slot.tasks.put(task, timeout=self.limits.timeout_seconds)
                _log("job_dispatched", pid=slot.process.pid, doc_id=task.source.doc_id,
                     job_id=task.source.job_id, status="PARSING")

    def _cancel(self, slot, reason):
        task = slot.task
        self.send("cancel", Frame(task.key, task.source.doc_id, task.source.job_id, 0, end=True, reason=reason))
        slot.writing = True
        slot.deadline = time.monotonic() + self.limits.timeout_seconds

    def _reports(self):
        while True:
            try:
                kind, value = self.outgoing.get_nowait()
            except Empty:
                return
            if kind == "fatal":
                raise PipelineError(f"Writer failed ({value})")
            if kind != "report":
                raise PipelineError("Unexpected writer message")
            key, report = value
            slot = self.active.pop(key)
            task = slot.task
            if (not self.database_mode and report.get("reason") in RETRYABLE
                    and task.attempt < self.limits.offline_attempts):
                self.sources.append((task.source, task.attempt + 1))
                report["status"] = "QUEUED"
            self._record(report)
            slot.task = None
            slot.writing = False

    def _check_workers(self):
        now = time.monotonic()
        for slot in self.active.values():
            if slot.writing:
                if now > slot.deadline:
                    raise PipelineError("Writer commit timed out")
            elif not slot.process.is_alive():
                _log("worker_exit", pid=slot.process.pid, exit_code=slot.process.exitcode)
                self._cancel(slot, "WORKER_EXIT")
            elif now > slot.deadline:
                _stop(slot.process)
                _log("worker_timeout", pid=slot.process.pid)
                self._cancel(slot, "WORKER_TIMEOUT")

    def run(self, workers):
        self._ack("ready", max(15, self.limits.timeout_seconds + 5))
        for _ in range(workers):
            self.slots.append(self._start_worker())
        renew_at = time.monotonic() + self.limits.lease_seconds / 3
        while True:
            self._reports()
            self._dispatch()
            if not self.active:
                break
            try:
                frame = self.results.get(timeout=POLL_SECONDS)
            except Empty:
                self._check_writer()
            else:
                slot = self.active.get(frame.key)
                if slot is not None and not slot.writing:
                    self.send("frame", frame)
                    if frame.end:
                        slot.writing = True
                        slot.deadline = time.monotonic() + self.limits.timeout_seconds
            self._reports()
            self._check_workers()
            if self.jobs and time.monotonic() >= renew_at:
                for slot in self.active.values():
                    if not slot.writing:
                        task = slot.task
                        self.jobs.renew(task.source.job_id, task.lease_token)
                renew_at = time.monotonic() + self.limits.lease_seconds / 3
        self.send("stop", None)
        generation, stats = self._ack("done", self.limits.timeout_seconds)
        return {
            "generation": generation, "stats": stats, "documents": self.reports, "reconciled": self.reconciled,
            "supervisor_pid": mp.current_process().pid, "writer_pid": self.writer.pid, "worker_pids": self.worker_pids,
            "queue_peak": {
                "task_per_worker": max([self.task_peak, *(slot.tasks.peak() for slot in self.slots)]),
                "result": self.results.peak(), "writer": self.incoming.peak(), "ack": self.outgoing.peak(),
            },
        }

    def close(self):
        for slot in self.slots:
            if slot.process.is_alive() and slot.task is None:
                slot.tasks.put(None, timeout=POLL_SECONDS)
                slot.process.join(timeout=1)
            _stop(slot.process)


def run_parallel(root: Path, sources=(), *, workers=2, max_tokens=256, limits=None, database_url=None,
                 pdf_root=None, compact=False, parser=parse_source, publish_hook=None):
    """Drain an imported batch or currently available durable jobs."""
    if workers < 1 or max_tokens < 1 or (database_url is not None and pdf_root is None):
        raise ValueError("Positive worker/token counts and a PDF root in durable mode are required")
    limits = limits or Limits()
    root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    ctx = mp.get_context("spawn")
    with QueueManager(ctx=ctx) as manager:
        results = manager.MeasuredQueue(limits.queue_items)
        incoming = manager.MeasuredQueue(limits.queue_items)
        outgoing = manager.MeasuredQueue(workers + 2)
        writer = ctx.Process(target=writer_main, args=(root, incoming, outgoing, limits, database_url, compact, publish_hook))
        writer.start()
        supervisor = None
        try:
            with open_jobs(database_url, limits.lease_seconds, limits.retry_seconds) as jobs:
                supervisor = Supervisor(ctx, manager, writer, incoming, outgoing, results, limits, max_tokens, parser,
                                        jobs, sources, database_url is not None, pdf_root.resolve() if pdf_root else None)
                report = supervisor.run(workers)
                writer.join(timeout=3)
                if writer.is_alive() or writer.exitcode != 0:
                    raise PipelineError("Writer did not shut down cleanly")
        finally:
            if supervisor is not None:
                supervisor.close()
            _stop(writer)
    report["wall_seconds"] = time.perf_counter() - started
    report["docs_per_second"] = sum(row["status"] == "INDEXED" for row in report["documents"]) / report["wall_seconds"]
    return report
