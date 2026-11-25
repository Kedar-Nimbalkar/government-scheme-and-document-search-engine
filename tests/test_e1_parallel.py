import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from search_shared.index_reader import read_snapshot

from engines.e1.jobs import open_jobs
from engines.e1.parallel import PipelineError, run_parallel
from engines.e1.processes import Limits
from engines.e1.reference import load_pdf_jobs, load_scheme_records, parse_source
from engines.e1.snapshot import _writer_lock, index_sources, merge_segments

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "e1-synthetic"
LIMITS = Limits(queue_items=1, chunk_bytes=64, timeout_seconds=15)
CRASH_DOC = "00000000-0000-4000-8000-000000000099"


def kill_worker_after_first_passage(source, max_tokens, emit, **limits):
    def send_then_exit(record):
        emit(record)
        os._exit(71)

    return parse_source(source, max_tokens, send_then_exit if source.doc_id == CRASH_DOC else emit, **limits)


def crash_before_manifest(phase):
    if phase == "before_manifest":
        os._exit(72)


def stall_worker(source, max_tokens, emit, **limits):
    if source.doc_id == CRASH_DOC:
        time.sleep(20)
    return parse_source(source, max_tokens, emit, **limits)


def crash_after_manifest(phase):
    if phase == "after_manifest":
        os._exit(73)


def pause_before_manifest(phase):
    if phase == "before_manifest":
        time.sleep(0.15)


@pytest.fixture
def sources():
    return load_pdf_jobs(FIXTURES / "pdf-jobs.json", FIXTURES) + load_scheme_records(FIXTURES / "schemes.csv")


def test_worker_exit_retries_with_no_partial_doc_and_batch_continues(tmp_path, sources):
    bad = replace(sources[0], doc_id=CRASH_DOC)
    malformed = tmp_path / "malformed.pdf"
    malformed.write_bytes(b"broken")
    corrupt = replace(sources[0], doc_id=str(uuid4()), pdf_path=malformed)
    root = tmp_path / "index"
    report = run_parallel(root, [bad, *sources, corrupt], limits=LIMITS, parser=kill_worker_after_first_passage)
    assert report["writer_pid"] != report["supervisor_pid"]
    assert len(set(report["worker_pids"])) >= 3
    assert report["writer_pid"] not in report["worker_pids"]
    assert report["queue_peak"]["result"] == report["queue_peak"]["writer"] == 1
    attempts = [item for item in report["documents"] if item["doc_id"] == CRASH_DOC]
    assert [(item["status"], item["reason"]) for item in attempts] == [
        ("QUEUED", "WORKER_EXIT"), ("FAILED", "WORKER_EXIT"),
    ]
    assert any(item.get("reason") == "PDF_CORRUPT" for item in report["documents"])
    manifest, _ = read_snapshot(root)
    assert manifest["committed_doc_ids"] == sorted(item.doc_id for item in sources)
    assert manifest["stats"]["N"] == 3


def test_stage_limit_fails_safely(tmp_path, sources):
    limits = replace(LIMITS, staged_bytes=80)
    report = run_parallel(tmp_path, sources[:1], limits=limits)
    assert report["documents"][0]["reason"] == "STAGE_LIMIT"
    manifest, segments = read_snapshot(tmp_path)
    assert manifest["committed_doc_ids"] == [] and segments == []


def test_worker_timeout_and_writer_lock(tmp_path, sources):
    with _writer_lock(tmp_path):
        with pytest.raises(PipelineError, match="Writer failed"):
            run_parallel(tmp_path, sources, limits=LIMITS)
    slow = replace(sources[0], doc_id=CRASH_DOC)
    limits = replace(LIMITS, timeout_seconds=2, offline_attempts=1)
    report = run_parallel(tmp_path, [slow, *sources], limits=limits, parser=stall_worker)
    assert any(row.get("reason") == "WORKER_TIMEOUT" for row in report["documents"])
    assert read_snapshot(tmp_path)[0]["stats"]["N"] == 3


@pytest.mark.parametrize("hook,published", [(crash_before_manifest, False), (crash_after_manifest, True)])
def test_manifest_crash_restart_and_duplicate_retry(tmp_path, sources, hook, published):
    index_sources(tmp_path, sources[:1])
    old_manifest = (tmp_path / "manifest.json").read_bytes()
    with pytest.raises(PipelineError, match="Writer exited"):
        run_parallel(tmp_path, sources[1:], limits=LIMITS, publish_hook=hook)
    manifest, _ = read_snapshot(tmp_path)
    assert (sources[1].doc_id in manifest["committed_doc_ids"]) is published
    if not published:
        assert (tmp_path / "manifest.json").read_bytes() == old_manifest
    assert (tmp_path / "manifest.previous.json").read_bytes() == old_manifest
    assert list(tmp_path.glob(".e1-stage-*"))
    replay = run_parallel(tmp_path, sources, limits=LIMITS)
    assert [row["status"] for row in replay["documents"]].count("INDEXED") == int(not published)
    assert replay["stats"]["N"] == 3
    assert not list(tmp_path.glob(".e1-*"))


def test_concurrent_read_and_heap_compaction_keep_old_segments(tmp_path, sources):
    index_sources(tmp_path, sources[:1])
    finished = threading.Event()
    seen = set()
    errors = []

    def query_snapshots():
        while not finished.is_set():
            try:
                manifest, segments = read_snapshot(tmp_path)
                assert len(merge_segments(segments)["postings"]["grant"]) == manifest["stats"]["N"]
                seen.add(manifest["generation"])
            except Exception as error:
                errors.append(error)
                return
            time.sleep(0.003)

    reader = threading.Thread(target=query_snapshots)
    reader.start()
    try:
        run_parallel(tmp_path, sources[1:], limits=LIMITS, publish_hook=pause_before_manifest)
    finally:
        finished.set()
        reader.join()
    assert not errors and 1 in seen
    before, segments = read_snapshot(tmp_path)
    run_parallel(tmp_path, limits=LIMITS, compact=True)
    after, [merged] = read_snapshot(tmp_path)
    assert (after["stats"], after["next_passage_id"]) == (before["stats"], before["next_passage_id"])
    assert merged == merge_segments(segments)
    assert all((tmp_path / entry["file"]).exists() for entry in before["segments"])


@pytest.fixture
def postgres():
    url = os.getenv("E1_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set E1_TEST_DATABASE_URL for standalone PostgreSQL tests")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    schema = "e1_test_" + uuid4().hex
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            isolated = make_conninfo(url, options=f"-c search_path={schema},public")
            with psycopg.connect(isolated, autocommit=True) as db:
                for migration in ("001_foundation.sql", "002_contract_v2.sql"):
                    db.execute((root / "db" / "migrations" / migration).read_text(), prepare=False)
                yield isolated, db
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.parametrize("hook,published", [(crash_before_manifest, False), (crash_after_manifest, True)])
def test_postgres_durable_crash_reconciliation(tmp_path, postgres, sources, hook, published):
    url, db = postgres
    doc = sources[0]
    department = db.execute("INSERT INTO department (dept_name, level) VALUES ('Test', 'State') RETURNING dept_id").fetchone()[0]
    db.execute("INSERT INTO scheme (scheme_id, dept_id, scheme_name, category, state) VALUES (%s,%s,'Test','Test','Test')",
               (doc.scheme_id, department))
    db.execute("INSERT INTO document (doc_id, scheme_id, title, source_type, file_path) VALUES (%s,%s,'Test','PDF',%s)",
               (doc.doc_id, doc.scheme_id, doc.pdf_path.name))
    db.execute("INSERT INTO index_job (job_id, doc_id) VALUES (%s,%s)", (doc.job_id, doc.doc_id))
    settings = replace(LIMITS, lease_seconds=2)
    with pytest.raises(PipelineError, match="Writer exited"):
        run_parallel(tmp_path, database_url=url, pdf_root=FIXTURES, limits=settings, publish_hook=hook)
    assert db.execute("SELECT status FROM index_job").fetchone()[0] == "COMMITTING"
    manifest, _ = read_snapshot(tmp_path)
    assert (doc.doc_id in manifest["committed_doc_ids"]) is published
    restarted = run_parallel(tmp_path, database_url=url, pdf_root=FIXTURES, limits=settings)
    assert restarted["reconciled"][0]["status"] == ("INDEXED" if published else "QUEUED")
    assert db.execute("SELECT status, attempts FROM index_job").fetchone() == ("INDEXED", 1 if published else 2)
    assert read_snapshot(tmp_path)[0]["committed_doc_ids"] == [doc.doc_id]


def test_postgres_worker_failure_retry_and_database_writer_lock(tmp_path, postgres, sources):
    url, db = postgres
    good = sources[0]
    bad = replace(good, doc_id=CRASH_DOC, job_id=str(uuid4()))
    department = db.execute("INSERT INTO department (dept_name, level) VALUES ('Test', 'State') RETURNING dept_id").fetchone()[0]
    db.execute("INSERT INTO scheme (scheme_id, dept_id, scheme_name, category, state) VALUES (%s,%s,'Test','Test','Test')",
               (good.scheme_id, department))
    for doc in (good, bad):
        db.execute("INSERT INTO document (doc_id, scheme_id, title, source_type, file_path) VALUES (%s,%s,'Test','PDF',%s)",
                   (doc.doc_id, good.scheme_id, f"{doc.doc_id}.pdf"))
        db.execute("INSERT INTO index_job (job_id, doc_id, max_attempts) VALUES (%s,%s,2)", (doc.job_id, doc.doc_id))
        (tmp_path / f"{doc.doc_id}.pdf").write_bytes(good.pdf_path.read_bytes())
    with open_jobs(url, 5, 0) as blocked:
        assert blocked.acquire_writer()
        with pytest.raises(PipelineError, match="Writer failed"):
            run_parallel(tmp_path / "index", database_url=url, pdf_root=tmp_path, limits=LIMITS)
    report = run_parallel(tmp_path / "index", database_url=url, pdf_root=tmp_path, limits=LIMITS,
                          parser=kill_worker_after_first_passage)
    assert [row["status"] for row in report["documents"] if row["doc_id"] == bad.doc_id] == ["QUEUED", "FAILED"]
    rows = db.execute("SELECT status, attempts FROM index_job ORDER BY status").fetchall()
    assert rows == [("FAILED", 2), ("INDEXED", 1)]
    assert read_snapshot(tmp_path / "index")[0]["committed_doc_ids"] == [good.doc_id]
