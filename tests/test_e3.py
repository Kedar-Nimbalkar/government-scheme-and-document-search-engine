import os
from pathlib import Path
from uuid import uuid4

import pytest
from search_shared.index_reader import read_snapshot
from search_shared.metadata import MetadataRepository

from db.e3.__main__ import seed
from db.e3.corpus import import_verified, load_catalog, scan
from engines.e1.jobs import open_jobs
from engines.e1.parallel import run_parallel

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "fixtures" / "e1-synthetic"
CATALOG = ROOT / "fixtures" / "e3-synthetic" / "catalog.json"


@pytest.fixture
def postgres():
    url = os.getenv("E3_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set E3_TEST_DATABASE_URL for standalone PostgreSQL tests")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    schema = f"e3_test_{uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            isolated = make_conninfo(url, options=f"-c search_path={schema},public")
            with psycopg.connect(isolated, autocommit=True) as db:
                for migration in sorted((ROOT / "db" / "migrations").glob("*.sql")):
                    db.execute(migration.read_text(encoding="utf-8"), prepare=False)
                yield db, isolated
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_inventory_quarantines_unverified_and_detects_duplicates(tmp_path):
    dataset, entries = load_catalog(CATALOG)
    valid = (SOURCE / "digital-and-mixed.pdf").read_bytes()
    (tmp_path / "digital-and-mixed.pdf").write_bytes(valid)
    (tmp_path / "duplicate.pdf").write_bytes(valid)
    (tmp_path / "image-only.pdf").write_bytes((SOURCE / "image-only.pdf").read_bytes())
    result = scan(tmp_path, dataset, entries)
    by_file = {item["path"]: item for item in result["records"]}
    assert result["count"] == 3 and result["unique_hashes"] == 2
    assert result["parseable_unique_hashes"] == 1
    assert result["counts"] == {"VERIFIED": 1, "QUARANTINED": 2}
    assert by_file["duplicate.pdf"]["duplicate_of"] == "digital-and-mixed.pdf"
    assert by_file["duplicate.pdf"]["reason"] == "UNVERIFIED_SCHEME_DEPARTMENT"
    assert by_file["image-only.pdf"]["reason"] == "PDF_NO_SEARCHABLE_TEXT"


def test_import_deduplication_count_reconciliation_and_e1_compatibility(postgres, tmp_path):
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    db, url = postgres
    dataset, entries = load_catalog(CATALOG)
    pilot = import_verified(db, SOURCE, tmp_path, dataset, entries, limit=1)
    assert pilot["counts"] == {"IMPORTED": 1}
    checked = import_verified(db, SOURCE, tmp_path, dataset, entries)
    assert checked["counts"] == {"DUPLICATE": 1, "QUARANTINED": 1}
    assert checked["records"][0]["doc_id"] == pilot["records"][0]["doc_id"]
    assert checked["records"][0]["job_id"] == pilot["records"][0]["job_id"]
    assert db.execute("""
        SELECT (SELECT count(*) FROM department), (SELECT count(*) FROM scheme),
               (SELECT count(*) FROM document), (SELECT count(*) FROM ingestion_job),
               (SELECT count(*) FROM document_import)
    """).fetchone() == (1, 1, 1, 1, 1)
    stored = db.execute("""
        SELECT title, file_path, issue_date, circular_no, version_status, artifact_kind
        FROM document JOIN document_import USING (doc_id)
    """).fetchone()
    assert stored[0] == "Synthetic fixture — Housing grant"
    assert (tmp_path / stored[1]).read_bytes() == (SOURCE / "digital-and-mixed.pdf").read_bytes()
    assert stored[2:] == (None, None, "UNKNOWN", "synthetic")

    doc_id = checked["records"][0]["doc_id"]
    metadata = MetadataRepository(db, [doc_id])
    assert metadata.get_matching_docs({"state": "Synthetic State"}) == set()
    assert metadata.get_metadata([doc_id]) == {}

    before = db.execute("SELECT updated_at FROM ingestion_job").fetchone()[0]
    db.execute((ROOT / "db" / "runtime-roles.sql").read_text(encoding="utf-8"), prepare=False)
    options = conninfo_to_dict(url)["options"]
    worker_url = make_conninfo(url, options=f"{options} -c role=team06_e1")
    index_root = tmp_path / "index"
    outcome = run_parallel(index_root, database_url=worker_url, pdf_root=tmp_path)
    assert [entry["status"] for entry in outcome["documents"]] == ["INDEXED"]
    manifest, _ = read_snapshot(index_root)
    assert manifest["committed_doc_ids"] == [doc_id]
    assert db.execute("SELECT status,updated_at > %s,error FROM ingestion_job", (before,)).fetchone() == (
        "INDEXED", True, None,
    )
    assert db.execute("SELECT version_status FROM document").fetchone()[0] == "UNKNOWN"
    metadata = MetadataRepository(db, manifest["committed_doc_ids"])
    assert metadata.get_matching_docs({"state": "Synthetic State", "category": "Housing"}) == {doc_id}
    assert metadata.get_matching_docs({"state": "' OR TRUE --"}) == set()
    assert metadata.get_metadata([doc_id])[doc_id]["superseded"] is None
    assert MetadataRepository(db, []).get_matching_docs({}) == set()


def test_sql_bookmark_roles_fks_restrict_and_rollback(postgres, tmp_path):
    import bcrypt
    from psycopg import errors

    db, _ = postgres
    dataset, entries = load_catalog(CATALOG)
    [item] = import_verified(db, SOURCE, tmp_path, dataset, entries, limit=1)["records"]
    doc, scheme = item["doc_id"], item["scheme_id"]
    creds = {
        "E3_TEST_CITIZEN_USERNAME": "citizen-fixture",
        "E3_TEST_CITIZEN_PASSWORD": "synthetic-test-password",
        "E3_TEST_DEPARTMENT_USERNAME": "department-fixture",
        "E3_TEST_DEPARTMENT_PASSWORD": "another-test-password",
    }
    assert seed(db, creds) == {"created": 2, "existing": 0}
    assert seed(db, creds) == {"created": 0, "existing": 2}
    user, digest = db.execute("SELECT user_id,password_hash FROM app_user WHERE role='citizen'").fetchone()
    assert bcrypt.checkpw(creds["E3_TEST_CITIZEN_PASSWORD"].encode(), digest.encode())
    assert db.execute("SELECT dept_id IS NOT NULL FROM app_user WHERE role='department'").fetchone()[0]
    with pytest.raises(errors.CheckViolation):
        db.execute("INSERT INTO app_user (username,password_hash,role) VALUES ('unscoped','hash','department')")
    db.execute("INSERT INTO bookmark (user_id,doc_id) VALUES (%s,%s)", (user, doc))
    db.execute("INSERT INTO bookmark (user_id,doc_id,page_no) VALUES (%s,%s,1)", (user, doc))
    db.execute("INSERT INTO bookmark (user_id,scheme_id) VALUES (%s,%s)", (user, scheme))
    for target_column, target in (("doc_id", doc), ("scheme_id", scheme)):
        with pytest.raises(errors.UniqueViolation):
            db.execute(f"INSERT INTO bookmark (user_id,{target_column}) VALUES (%s,%s)", (user, target))
    with pytest.raises(errors.UniqueViolation):
        db.execute("INSERT INTO bookmark (user_id,doc_id,page_no) VALUES (%s,%s,1)", (user, doc))
    with pytest.raises(errors.CheckViolation):
        db.execute("INSERT INTO bookmark (user_id,doc_id,scheme_id) VALUES (%s,%s,%s)", (user, doc, scheme))
    with pytest.raises(errors.CheckViolation):
        db.execute("INSERT INTO bookmark (user_id,scheme_id,page_no) VALUES (%s,%s,1)", (user, scheme))
    with pytest.raises(errors.CheckViolation):
        db.execute("INSERT INTO bookmark (user_id) VALUES (%s)", (user,))
    with pytest.raises(errors.ForeignKeyViolation):
        db.execute("INSERT INTO bookmark (user_id,scheme_id) VALUES (%s,%s)", (user, uuid4()))
    for path, content_hash in (("../outside.pdf", "a" * 64), ("imports/invalid.pdf", "not-sha256")):
        with pytest.raises(errors.CheckViolation):
            db.execute("""
                INSERT INTO document (scheme_id,title,source_type,file_path,content_hash) VALUES (%s,'Bad','PDF',%s,%s)
            """, (scheme, path, content_hash))
    with pytest.raises(errors.ForeignKeyViolation):
        db.execute("DELETE FROM document WHERE doc_id=%s", (doc,))
    with pytest.raises(errors.ForeignKeyViolation):
        db.execute("DELETE FROM scheme WHERE scheme_id=%s", (scheme,))
    with pytest.raises(errors.ForeignKeyViolation):
        db.execute("DELETE FROM department WHERE dept_id=(SELECT dept_id FROM scheme WHERE scheme_id=%s)", (scheme,))
    assert db.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 0


def test_unresolved_metadata_and_storage_failure_roll_back(postgres, tmp_path):
    db, _ = postgres
    dataset, entries = load_catalog(CATALOG)
    filename = "digital-and-mixed.pdf"
    entry = {**entries[filename], "department": ""}
    rejected = import_verified(db, SOURCE, tmp_path, dataset, {filename: entry})
    assert rejected["records"][0]["reason"] == "MISSING_DEPARTMENT"

    (tmp_path / "imports").write_text("blocks import directory", encoding="utf-8")
    with pytest.raises(FileExistsError):
        import_verified(db, SOURCE, tmp_path, dataset, entries, limit=1)
    for table in ("department", "scheme", "document", "ingestion_job", "document_import", "corpus_dataset"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_restricted_job_claims_recovery_and_snapshot_filtered_metadata(postgres):
    import psycopg
    from psycopg import errors
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    db, url = postgres
    db.execute((ROOT / "db" / "runtime-roles.sql").read_text(encoding="utf-8"), prepare=False)
    options = conninfo_to_dict(url)["options"]
    writer_url = make_conninfo(url, options=f"{options} -c role=team06_e1")
    search_url = make_conninfo(url, options=f"{options} -c role=team06_e2")

    dept = db.execute("INSERT INTO department(dept_name,level) VALUES ('Housing','State') RETURNING dept_id").fetchone()[0]
    # Simulate a legacy unknown state to exercise SQL NULL comparison semantics.
    db.execute("ALTER TABLE scheme ALTER COLUMN state DROP NOT NULL")
    docs = {}
    for label, state, status in (("known", "Karnataka", "QUEUED"), ("unknown", None, "QUEUED"),
                                 ("foreign", "Goa", "INDEXED"), ("absent", "Karnataka", "INDEXED")):
        scheme_id = db.execute("""
            INSERT INTO scheme(dept_id,scheme_name,category,state) VALUES (%s,%s,'Housing',%s) RETURNING scheme_id
        """, (dept, label, state)).fetchone()[0]
        doc_id = db.execute("""
            INSERT INTO document(scheme_id,title,source_type) VALUES (%s,%s,'CSV') RETURNING doc_id
        """, (scheme_id, label)).fetchone()[0]
        db.execute("INSERT INTO ingestion_job(doc_id,status) VALUES (%s,%s)", (doc_id, status))
        docs[label] = str(doc_id)

    with open_jobs(writer_url, 10, 0) as first, open_jobs(writer_url, 10, 0) as second:
        with first.connection.transaction():
            first_job, first_doc, _, _, _, _, _ = first.claim()
            # The first row is still locked while another transaction claims.
            second_job, second_doc, _, lease, _, _, _ = second.claim()
            assert first_doc != second_doc
            assert second.claim() is None
            assert second.begin(second_job, lease)
            assert second.finish(second_job, lease)
        db.execute("UPDATE ingestion_job SET lease_until=now()-interval '1 second' WHERE job_id=%s", (first_job,))
        assert first.acquire_writer()
        assert first.reconcile(set()) == [{"doc_id": str(first_doc), "job_id": str(first_job), "status": "QUEUED"}]
        retried, _, attempts, lease, _, _, _ = first.claim()
        assert retried == first_job and attempts == 2
        assert first.begin(retried, lease)
        assert first.finish(retried, lease)

    db.execute("UPDATE document SET version_status='SUPERSEDED' WHERE doc_id=%s", (docs["known"],))
    event = db.execute("SELECT details FROM operational_log WHERE event='INGESTION_ERROR'").fetchone()[0]
    assert event["reason"] == "LEASE_EXPIRED" and event["status"] == "QUEUED"
    captured = [docs[name] for name in ("known", "unknown", "foreign")]
    with psycopg.connect(search_url, autocommit=True) as reader:
        metadata = MetadataRepository(reader, captured)
        assert metadata.get_matching_docs({}) == set(captured)
        assert metadata.get_matching_docs({"state": "Karnataka", "department": "Housing"}) == {docs["known"]}
        assert metadata.get_matching_docs({"state": "Kerala"}) == set()
        result = metadata.get_metadata(list(docs.values()) + [str(uuid4())])
        assert set(result) == set(captured)
        assert result[docs["known"]]["superseded"] is True
        assert result[docs["unknown"]]["issue_date"] is None
        with pytest.raises(errors.InsufficientPrivilege):
            reader.execute("SELECT password_hash FROM app_user")
