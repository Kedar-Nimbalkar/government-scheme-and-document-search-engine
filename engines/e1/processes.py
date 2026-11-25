"""Spawn targets and the bounded worker-to-writer protocol."""

import json
import logging
import math
import multiprocessing as mp
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full
from tempfile import NamedTemporaryFile

from .jobs import open_jobs
from .reference import Source, compile_segment, parse_source, source_report
from .snapshot import SnapshotWriter, _json_bytes, _writer_lock

RETRYABLE = {"WORKER_EXIT", "WORKER_TIMEOUT", "WORKER_ERROR", "PDF_UNREADABLE"}
POLL_SECONDS = 0.1


@dataclass(frozen=True)
class Limits:
    chunk_bytes: int = 64 * 1024
    staged_bytes: int = 8 * 1024 * 1024
    input_bytes: int = 16 * 1024 * 1024
    task_bytes: int = 256 * 1024
    queue_items: int = 4
    timeout_seconds: float = 60
    lease_seconds: int = 30
    retry_seconds: int = 0
    offline_attempts: int = 2

    def __post_init__(self):
        if min(self.chunk_bytes, self.staged_bytes, self.input_bytes, self.task_bytes,
               self.queue_items, self.timeout_seconds, self.lease_seconds, self.offline_attempts) <= 0:
            raise ValueError("Indexing limits must be positive")
        if not math.isfinite(self.timeout_seconds) or self.retry_seconds < 0 or self.chunk_bytes > self.staged_bytes:
            raise ValueError("Invalid staging or retry limit")


@dataclass(frozen=True)
class Task:
    key: int
    source: Source
    attempt: int
    lease_token: str | None = None


@dataclass(frozen=True)
class Frame:
    key: int
    doc_id: str
    job_id: str | None
    sequence: int
    payload: bytes = b""
    end: bool = False
    reason: str | None = None


class StageLimit(Exception):
    pass


def _put(queue, item):
    while mp.parent_process().is_alive():
        try:
            queue.put(item, timeout=POLL_SECONDS)
            return
        except Full:
            continue
    raise SystemExit


def worker_main(tasks, results, limits: Limits, max_tokens: int, parser=parse_source):
    logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)
    while mp.parent_process().is_alive():
        try:
            task = tasks.get(timeout=POLL_SECONDS)
        except Empty:
            continue
        if task is None:
            return
        source = task.source
        sequence = total = 0

        def send(payload=b"", *, end=False, reason=None):
            nonlocal sequence
            _put(results, Frame(task.key, source.doc_id, source.job_id, sequence, payload, end, reason))
            sequence += 1

        buffer = bytearray()

        def emit(record):
            nonlocal total
            encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            total += len(encoded)
            if total > limits.staged_bytes:
                raise StageLimit
            view = memoryview(encoded)
            while view:
                needed = limits.chunk_bytes - len(buffer)
                buffer.extend(view[:needed])
                view = view[needed:]
                if len(buffer) == limits.chunk_bytes:
                    send(bytes(buffer))
                    buffer.clear()

        try:
            path = source.pdf_path or source.article_path
            if path is not None and path.stat().st_size > limits.input_bytes:
                raise StageLimit
            report = parser(source, max_tokens, emit, max_text_bytes=limits.staged_bytes)
            emit(report)
            if buffer:
                send(bytes(buffer))
            send(end=True)
        except StageLimit:
            send(end=True, reason="STAGE_LIMIT")
        except OSError:
            send(end=True, reason="PDF_UNREADABLE")
        except Exception:
            send(end=True, reason="WORKER_ERROR")


class StagedDocument:
    def __init__(self, root: Path, task: Task, limits: Limits):
        self.file = NamedTemporaryFile(dir=root, prefix=".e1-stage-", mode="w+b", delete=False)
        self.task = task
        self.limits = limits
        self.sequence = self.size = 0

    def receive(self, frame: Frame):
        if (frame.sequence != self.sequence or frame.doc_id != self.task.source.doc_id
                or frame.job_id != self.task.source.job_id or len(frame.payload) > self.limits.chunk_bytes):
            raise ValueError("Invalid worker frame")
        self.sequence += 1
        self.size += len(frame.payload)
        if self.size > self.limits.staged_bytes:
            raise StageLimit
        self.file.write(frame.payload)

    def records(self):
        self.file.seek(0)
        return map(json.loads, self.file)

    def close(self):
        self.file.close()
        Path(self.file.name).unlink(missing_ok=True)


def _complete(writer, jobs, stage: StagedDocument, reason: str | None):
    task = stage.task
    source = task.source
    job = (source.job_id, task.lease_token)
    records = []
    if reason:
        report = {**source_report(source), "status": "FAILED", "reason": reason}
    else:
        parsed = list(stage.records())
        if not parsed or not isinstance(parsed[-1], dict) or parsed[-1].get("doc_id") != source.doc_id:
            raise ValueError("Missing document end report")
        *records, report = parsed
        if report["status"] == "INDEXED" and (not records or len(records) != report["passages"]):
            raise ValueError("Incomplete parsed document")

    duplicate = source.doc_id in writer.committed
    if report["status"] == "FAILED" and not duplicate:
        if jobs:
            outcome = jobs.fail(*job, report["reason"] in RETRYABLE, report["reason"])
            report["status"] = outcome[1] if outcome else "LEASE_LOST"
        return report

    if jobs and not jobs.begin(*job):
        return {**report, "status": "LEASE_LOST"}
    if duplicate:
        report = {**source_report(source), "status": "SKIPPED_DUPLICATE"}

    def publish():
        if not duplicate:
            segment, next_id = compile_segment([(source.doc_id, records)], writer.next_id)
            writer.append(segment, {source.doc_id}, next_id)

    if jobs:
        # Fence publication with the current lease's row lock. COMMITTING
        # persisted above, so a crash on either side can be reconciled.
        with jobs.connection.transaction():
            if not jobs.renew(*job):
                return {**report, "status": "LEASE_LOST"}
            publish()
            if not jobs.finish(*job):
                raise RuntimeError("Could not finish durable indexing job")
        report["status"] = "INDEXED"
    else:
        publish()
    return report


def _recover(writer, jobs, timeout, outgoing):
    deadline = time.monotonic() + timeout
    while True:
        for outcome in jobs.reconcile(writer.committed):
            _put(outgoing, ("reconciled", outcome))
        delay = jobs.recovery_delay()
        if delay is None:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0 or delay > remaining:
            raise TimeoutError("Outstanding leases exceed recovery timeout")
        if not mp.parent_process().is_alive():
            raise SystemExit
        time.sleep(min(max(0.01, delay), remaining, 0.25))


def writer_main(root, incoming, outgoing, limits, database_url, compact, publish_hook):
    staged = {}
    try:
        with _writer_lock(root), open_jobs(database_url, limits.lease_seconds, limits.retry_seconds) as jobs:
            if jobs and not jobs.acquire_writer():
                raise RuntimeError("Another durable writer owns this database schema")
            writer = SnapshotWriter(root, publish_hook)
            writer.append({}, set(), writer.next_id)
            if jobs:
                _recover(writer, jobs, limits.timeout_seconds, outgoing)
            _put(outgoing, ("ready", None))
            while mp.parent_process().is_alive():
                try:
                    action, item = incoming.get(timeout=POLL_SECONDS)
                except Empty:
                    continue
                if action == "stop":
                    if compact:
                        writer.compact()
                    _put(outgoing, ("done", (writer.manifest["generation"], writer.manifest["stats"])))
                    return
                if action == "start":
                    staged[item.key] = StagedDocument(root, item, limits)
                    continue
                stage = staged[item.key]
                if action == "frame":
                    stage.receive(item)
                    if not item.end:
                        continue
                try:
                    report = _complete(writer, jobs, stage, item.reason)
                finally:
                    staged.pop(item.key).close()
                _put(outgoing, ("report", (item.key, report)))
    except Exception as error:
        # No file paths, document text, connection strings, or raw SQL errors.
        _put(outgoing, ("fatal", type(error).__name__))
    finally:
        for stage in staged.values():
            stage.close()
