"""Inspect local PDFs and import a catalog of reviewed metadata."""

import hashlib
import json
import logging
import multiprocessing
import os
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from datetime import date
from io import BytesIO
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject
from psycopg.errors import UniqueViolation
from search_shared.tokenizer import tokenize

MAX_BYTES = 16 * 1024 * 1024


class Rejected(ValueError):
    pass


def required(fields, key):
    value = fields.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Rejected(f"MISSING_{key.upper()}")
    return value.strip()


def safe_url(value):
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme in {"http", "https"} and url.hostname and url.username is None and url.password is None:
            return value
    except ValueError:
        pass
    return None


def load_catalog(path):
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or not isinstance(catalog.get("documents"), list):
        raise Rejected("INVALID_CATALOG")
    dataset = {key: required(catalog, key) for key in ("dataset_id", "revision", "license")}
    entries = {}
    for entry in catalog["documents"]:
        if not isinstance(entry, dict):
            raise Rejected("INVALID_CATALOG_ENTRY")
        name = required(entry, "path")
        relative = PurePosixPath(name)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name
                or relative.as_posix() != name or relative.suffix.lower() != ".pdf" or name in entries):
            raise Rejected("INVALID_OR_DUPLICATE_CATALOG_PATH")
        entries[name] = entry
    return dataset, entries


def read_pdf(path):
    try:
        with path.open("rb") as source:
            payload = source.read(MAX_BYTES + 1)
    except OSError as error:
        raise Rejected("PDF_UNREADABLE") from error
    if len(payload) > MAX_BYTES:
        raise Rejected("PDF_LIMIT")
    return payload


def probe(payload):
    try:
        pdf = PdfReader(BytesIO(payload), strict=True)
        if pdf.is_encrypted:
            raise Rejected("PDF_ENCRYPTED")
        page_count = len(pdf.pages)
        if page_count > 10_000:
            raise Rejected("PDF_LIMIT")
        text_pages = 0
        links = set()
        for page in pdf.pages:
            if tokenize(page.extract_text()):
                text_pages += 1
            try:
                annotations = page["/Annots"]
            except (KeyError, PyPdfError, TypeError, ValueError):
                annotations = None
            for reference in annotations if isinstance(annotations, ArrayObject) else ():
                try:
                    link = safe_url(reference.get_object().get("/A", {}).get("/URI"))
                except (PyPdfError, TypeError, ValueError, AttributeError, KeyError):
                    continue
                if link:
                    links.add(link)
        if not text_pages:
            raise Rejected("PDF_NO_SEARCHABLE_TEXT")
        return {"pages": page_count, "text_pages": text_pages, "embedded_links": sorted(links)}
    except Rejected:
        raise
    except (PyPdfError, ValueError, TypeError, IndexError, KeyError, AttributeError, RecursionError) as error:
        raise Rejected("PDF_CORRUPT") from error


def reviewed(entry, digest):
    if entry is None:
        raise Rejected("UNVERIFIED_SCHEME_DEPARTMENT")
    if entry.get("sha256") != digest:
        raise Rejected("HASH_MISMATCH")
    values = {key: required(entry, key) for key in (
        "title", "scheme_name", "department", "level", "category", "state", "artifact_kind",
    )}
    kind = values["artifact_kind"]
    if kind not in {"official_document", "dataset_snapshot", "synthetic"} or values["level"] not in {"Central", "State"}:
        raise Rejected("INVALID_CLASSIFICATION")
    for key in ("source_url", "evidence_url"):
        supplied = entry.get(key)
        values[key] = safe_url(supplied)
        if supplied is not None and values[key] is None:
            raise Rejected(f"INVALID_{key.upper()}")
    if kind != "synthetic" and values["evidence_url"] is None:
        raise Rejected("MISSING_EVIDENCE_URL")
    try:
        issued = entry.get("issue_date")
        values["issue_date"] = date.fromisoformat(issued) if issued is not None else None
        if issued is not None and values["issue_date"].isoformat() != issued:
            raise ValueError("Noncanonical date")
    except (ValueError, TypeError) as error:
        raise Rejected("INVALID_ISSUE_DATE") from error
    values["circular_no"] = required(entry, "circular_no") if entry.get("circular_no") is not None else None
    if kind == "dataset_snapshot":
        values["title"] = "Dataset snapshot — " + values["title"]
    elif kind == "synthetic":
        values["title"] = "Synthetic fixture — " + values["title"]
    return values


def _quiet_pdf_logs():
    logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)


def _probe_result(payload):
    try:
        return probe(payload), None
    except Rejected as error:
        return {}, str(error)


def scan(root, dataset, entries, workers=1):
    _quiet_pdf_logs()
    results = []
    by_hash = {}
    encountered = set()
    pending = deque()
    executor = ProcessPoolExecutor(
        max_workers=workers, mp_context=multiprocessing.get_context("spawn"), initializer=_quiet_pdf_logs,
    ) if workers > 1 else nullcontext(None)
    with executor as pool:
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() != ".pdf" or not path.is_file():
                continue
            name = path.relative_to(root).as_posix()
            encountered.add(name)
            record = {"path": name, "status": "QUARANTINED"}
            results.append(record)
            try:
                payload = read_pdf(path)
            except Rejected as error:
                record["reason"] = str(error)
                continue
            digest = hashlib.sha256(payload).hexdigest()
            record["sha256"] = digest
            if digest in by_hash:
                continue
            if pool is None:
                by_hash[digest] = (name, *_probe_result(payload))
            else:
                by_hash[digest] = None
                pending.append((digest, name, pool.submit(_probe_result, payload)))
                if len(pending) >= workers * 2:
                    key, first, future = pending.popleft()
                    by_hash[key] = (first, *future.result())
        for digest, first, future in pending:
            by_hash[digest] = (first, *future.result())

    for record in results:
        digest = record.get("sha256")
        if digest is None:
            continue
        name = record["path"]
        first, details, reason = by_hash[digest]
        record.update(details)
        record["parseable"] = reason is None
        if first != name:
            record["duplicate_of"] = first
        try:
            if reason:
                raise Rejected(reason)
            record.update(status="VERIFIED", metadata=reviewed(entries.get(name), digest))
        except Rejected as error:
            record["reason"] = str(error)
    for name in sorted(entries.keys() - encountered):
        results.append({"path": name, "status": "QUARANTINED", "reason": "FILE_NOT_FOUND"})
    result = report(dataset, results)
    result["unique_hashes"] = len(by_hash)
    result["parseable_unique_hashes"] = sum(reason is None for _, _, reason in by_hash.values())
    return result


def report(dataset, records):
    return {"dataset": dataset, "count": len(records), "counts": dict(Counter(r["status"] for r in records)),
            "records": records}


def existing_document(db, digest):
    existing = db.execute("""
        SELECT d.doc_id, j.job_id FROM document d
        LEFT JOIN ingestion_job j USING (doc_id) WHERE d.content_hash = %s
    """, (digest,)).fetchone()
    if existing:
        return {"status": "DUPLICATE", "doc_id": str(existing[0]), "job_id": str(existing[1]) if existing[1] else None}
    return None


def store_pdf(pdf_root, digest, payload):
    directory = pdf_root / "imports"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve().parent != pdf_root.resolve():
        raise OSError("Import storage directory escapes PDF_ROOT")
    target = directory / f"{digest}.pdf"
    if target.is_symlink():
        raise OSError("Import storage does not accept symbolic links")
    if target.exists():
        if hashlib.sha256(read_pdf(target)).hexdigest() != digest:
            raise OSError("Stored PDF checksum mismatch")
        return
    with NamedTemporaryFile(dir=directory, prefix=".e3-", delete=False) as stage:
        try:
            stage.write(payload)
            stage.flush()
            os.fsync(stage.fileno())
            stage.close()
            os.replace(stage.name, target)
        finally:
            Path(stage.name).unlink(missing_ok=True)
    if os.name != "nt":
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def register_document(db, pdf_root, dataset, record, payload):
    """Persist linkage, provenance, and one durable job with an original PDF."""
    digest = record["sha256"]
    item = record["metadata"]
    with db.transaction():
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"team06/import/{digest}",))
        existing = existing_document(db, digest)
        if existing:
            return existing

        dept = (item["department"], item["level"])
        db.execute("INSERT INTO department (dept_name, level) VALUES (%s,%s) ON CONFLICT DO NOTHING", dept)
        dept_id = db.execute("SELECT dept_id FROM department WHERE dept_name=%s AND level=%s", dept).fetchone()[0]
        identity = (dept_id, item["scheme_name"], item["state"])
        stable_id = uuid5(NAMESPACE_URL, json.dumps(["team06/scheme", *dept, item["scheme_name"], item["state"]]))
        db.execute("""
            INSERT INTO scheme (scheme_id,dept_id,scheme_name,state,category) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (dept_id,scheme_name,state) DO NOTHING
        """, (stable_id, *identity, item["category"]))
        scheme_id, category = db.execute(
            "SELECT scheme_id,category FROM scheme WHERE dept_id=%s AND scheme_name=%s AND state=%s", identity,
        ).fetchone()
        if category != item["category"]:
            raise Rejected("SCHEME_METADATA_CONFLICT")
        release = (dataset["dataset_id"], dataset["revision"])
        db.execute("INSERT INTO corpus_dataset VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (*release, dataset["license"]))
        if db.execute("SELECT license FROM corpus_dataset WHERE dataset_id=%s AND revision=%s", release).fetchone()[0] != dataset["license"]:
            raise Rejected("DATASET_LICENSE_CONFLICT")
        doc_id = uuid5(NAMESPACE_URL, f"team06/pdf/sha256/{digest}")
        file_path = f"imports/{digest}.pdf"
        db.execute("""
            INSERT INTO document (doc_id,scheme_id,title,circular_no,issue_date,file_path,source_url,source_type,content_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'PDF',%s)
        """, (doc_id, scheme_id, item["title"], item["circular_no"], item["issue_date"], file_path, item["source_url"], digest))
        db.execute("INSERT INTO document_import VALUES (%s,%s,%s,%s,%s,%s)",
                   (doc_id, *release, record["path"], item["artifact_kind"], item["evidence_url"]))
        job_id = db.execute("INSERT INTO ingestion_job (doc_id) VALUES (%s) RETURNING job_id", (doc_id,)).fetchone()[0]
        store_pdf(pdf_root, digest, payload)
    return {"status": "IMPORTED", "doc_id": str(doc_id), "job_id": str(job_id), "scheme_id": str(scheme_id)}


def import_verified(db, root, pdf_root, dataset, entries, limit=None):
    records = []
    for name, entry in list(entries.items())[:limit]:
        record = {"path": name, "status": "QUARANTINED"}
        records.append(record)
        try:
            payload = read_pdf(root / name)
            digest = hashlib.sha256(payload).hexdigest()
            record["sha256"] = digest
            record.update(probe(payload))
            record["metadata"] = reviewed(entry, digest)
            record.update(register_document(db, pdf_root, dataset, record, payload))
        except Rejected as error:
            record["reason"] = str(error)
        except UniqueViolation as error:
            if error.diag.constraint_name != "document_content_hash_key":
                raise
            record.update(existing_document(db, digest) or {"reason": "DUPLICATE_CHANGED_DURING_IMPORT"})
    return report(dataset, records)
