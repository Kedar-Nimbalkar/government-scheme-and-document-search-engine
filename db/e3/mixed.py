"""Discover and register the local mixed corpus without modifying raw inputs."""

import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import psycopg

from engines.e1.pdf import extract_pdf
from engines.e1.source_cleaning import clean_article, clean_snapshot_page, snapshot_header
from search_shared.metadata import normalize_filter

from .corpus import store_pdf

TYPES = {"scheme_articles": (".txt", "SCHEME_ARTICLE"), "scheme_pdfs": (".pdf", "SCHEME_PDF")}
REGIONS = (
    "Andhra Pradesh|Arunachal Pradesh|Assam|Bihar|Chandigarh|Chhattisgarh|Delhi|Goa|Gujarat|Haryana|"
    "Himachal Pradesh|Jammu and Kashmir|Jharkhand|Karnataka|Kerala|Madhya Pradesh|Maharashtra|Manipur|"
    "Meghalaya|Mizoram|Nagaland|Odisha|Punjab|Rajasthan|Sikkim|Tamil Nadu|Telangana|Tripura|Uttar Pradesh|"
    "Uttarakhand|West Bengal|Puducherry|Lakshadweep|Ladakh|Andaman and Nicobar Islands|"
    "Dadra and Nagar Haveli and Daman and Diu|Central"
).split("|")
FOLDERS = {region.lower().replace(" ", "-"): region for region in REGIONS}
FOLDERS.update({"jammu-kashmir": "Jammu and Kashmir", "tamilnadu": "Tamil Nadu"})
PDF_SCOPES = {region: region for region in REGIONS}
PDF_SCOPES.update({"Central Government": "Central", "Jammu & Kashmir": "Jammu and Kashmir",
                   "Andaman & Nicobar Islands": "Andaman and Nicobar Islands"})
SIZE_LIMIT = 16 * 1024 * 1024
URL = re.compile(r"""https?://[^\s<>"\u201c\u201d]+""")
COPY = re.compile(r"(?:[ _-]copy)(?:[ _-]?\(?\d+\)?)?$", re.I)


def read_input(path):
    with path.open("rb") as source:
        body = source.read(SIZE_LIMIT + 1)
    if not body or len(body) > SIZE_LIMIT:
        raise ValueError("EMPTY_OR_LARGE_FILE")
    return body


def discover(root):
    inventory, skipped = [], []
    for folder, (suffix, kind) in TYPES.items():
        base = root / folder
        if not base.is_dir():
            raise ValueError(f"Dataset directory missing: {folder}")
        groups = defaultdict(list)
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if path.suffix.lower() != suffix or path.is_symlink() or not path.resolve().is_relative_to(root):
                skipped.append({"path": relative, "reason": "UNSUPPORTED_FILE"})
                continue
            try:
                digest = hashlib.sha256(read_input(path)).hexdigest()
            except (OSError, ValueError):
                skipped.append({"path": relative, "reason": "EMPTY_OVERSIZE_OR_UNREADABLE"})
                continue
            groups[digest].append(relative)
        for digest, paths in sorted(groups.items()):
            paths.sort(key=lambda item: (bool(COPY.search(Path(item).stem)), item.casefold(), item))
            regions = sorted({FOLDERS[part.lower()] for item in paths for part in Path(item).parts[1:-1]
                              if part.lower() in FOLDERS}) if kind == "SCHEME_ARTICLE" else []
            inventory.append(dict(source_type=kind, sha256=digest, paths=paths, regions=regions,
                                  source_id=str(uuid5(NAMESPACE_URL, "team06:mixed:" + kind + ":" + digest))))
    return inventory, skipped


def references(text):
    found = set()
    for match in URL.finditer(text):
        value = match[0].rstrip(".,;:)]}")
        try:
            parsed = urlsplit(value)
            if parsed.hostname and not parsed.username and "\\" not in value and len(value) <= 2048:
                found.add(value)
        except ValueError:
            continue
    return sorted(found)[:30]


def describe_article(record, payload):
    cleaned = clean_article(payload.decode("utf-8-sig"))
    if not any(character.isalnum() for character in cleaned):
        raise ValueError("NO_SEARCHABLE_TEXT")
    lines = cleaned.splitlines()
    heading = next((line for line in lines[:18] if 9 <= len(line) <= 130
                    and re.search(r"\b(scheme|yojana|scholarship|pension|subsidy|abhiyan)\b", line, re.I)
                    and not line.endswith((".", ":", "?", "!"))), None)
    # A heading describes the article. Multi-scheme articles lack a reliable scheme identity.
    title = heading or next((line for line in lines if 10 <= len(line) <= 130 and not line.endswith(".")), None)
    title = title or re.split(r"(?<=[.!?])\s", lines[0], maxsplit=1)[0][:140].rstrip()
    record.update(title=title, displayed_title=None, references=references(cleaned),
                  evidence={"title_method": "article_heading" if heading else "article_text",
                            "verification": "SOURCE_EXTRACTED", "directory_regions": record["regions"]})
    return cleaned.encode("utf-8")


def describe_pdf(record, path, payload):
    if not payload.startswith(b"%PDF-"):
        raise ValueError("INVALID_PDF_SIGNATURE")
    pdf = extract_pdf(path, max_text_bytes=4 * 1024 * 1024)
    title, after_header = snapshot_header(pdf.pages[0][1])
    layout_title = title if title and len(title) <= 240 else None
    title = layout_title or path.stem
    scope_label = None
    department = None
    raw_tags = None
    if layout_title:
        title_pattern = r"\s+".join(re.escape(part) for part in title.split())
        repeated = re.search(title_pattern, after_header, re.I)
        if repeated:
            scope_label = " ".join(after_header[:repeated.start()].split())
            scope_lookup = {key.casefold(): value for key, value in PDF_SCOPES.items()}
            scope = scope_lookup.get(scope_label.casefold())
            if scope:
                record["regions"] = [scope]
            elif re.fullmatch(r"Ministry\s+of\s+.{2,100}", scope_label, re.I):
                department = re.sub(r"(?i)^Ministry of ", "Ministry of ", scope_label)
                record["regions"] = ["Central"]
            tags = re.match(r"(.{0,200}?)Details", " ".join(after_header[repeated.end():].split()))
            if tags:
                raw_tags = tags[1] or None
    sections = [clean_snapshot_page(text) for _, text in pdf.pages]
    joined = "\n".join(sections)
    if not any(character.isalnum() for character in joined):
        raise ValueError("NO_SEARCHABLE_TEXT")
    stamp = re.match(r"D:(\d{4})(\d{2})(\d{2})", pdf.creation_date or "")
    try:
        export = date(*map(int, stamp.groups())).isoformat() if stamp else None
    except ValueError:
        export = None
    record.update(title=title, displayed_title=layout_title, department=department,
                  page_count=len(pdf.pages) + len(pdf.omitted_pages), export_date=export,
                  references=references(joined),
                  evidence={"verification": "SOURCE_EXTRACTED", "header_scope": scope_label,
                            "header_title": layout_title, "raw_tags": raw_tags, "creator": pdf.creator, "creation_date": pdf.creation_date})


def sample(records, count):
    groups = defaultdict(deque)
    for item in records:
        groups[(item["source_type"], tuple(item["regions"]))].append(item)
    selected, totals = [], Counter()
    while groups:
        for key in sorted(groups):
            kind = key[0]
            if totals[kind] < count:
                selected.append(groups[key].popleft())
                totals[kind] += 1
            if not groups[key] or totals[kind] >= count:
                del groups[key]
    return selected


def store_article(root, digest, payload, *, pending):
    directory = root / "articles"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}.txt"
    if directory.resolve().parent != root.resolve() or target.is_symlink():
        raise OSError("UNSAFE_ARTICLE_STORAGE")
    if target.exists():
        if target.read_bytes() == payload:
            return
        if not pending:
            raise OSError("STORED_ARTICLE_MISMATCH")
    with NamedTemporaryFile(dir=directory, delete=False) as stage:
        try:
            stage.write(payload)
            stage.flush()
            os.fsync(stage.fileno())
            stage.close()
            os.replace(stage.name, target)
        finally:
            Path(stage.name).unlink(missing_ok=True)


def scheme_links(db):
    links = defaultdict(list)
    for scheme_id, title, state in db.execute("SELECT scheme_id,scheme_name,state FROM scheme"):
        links[(normalize_filter(title), normalize_filter(state))].append(scheme_id)
    return links


def register(db, root, item, payload, links):
    kind, digest = item["source_type"], item["sha256"]
    scope = item["regions"][0] if len(item["regions"]) == 1 else None
    candidates = links[(normalize_filter(item.get("displayed_title")), normalize_filter(scope))] if scope else []
    scheme_id = candidates[0] if len(candidates) == 1 else None
    file_path = f"articles/{digest}.txt" if kind == "SCHEME_ARTICLE" else f"imports/{digest}.pdf"
    with db.transaction():
        # The unique content hash arbitrates concurrent imports.
        created = db.execute("""
            INSERT INTO document(doc_id,scheme_id,title,file_path,source_type,content_hash)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(content_hash) DO NOTHING RETURNING doc_id
        """, (item["source_id"], scheme_id, item["title"], file_path, kind, digest)).fetchone()
        doc_id, prior_type = db.execute("SELECT doc_id,source_type FROM document WHERE content_hash=%s FOR UPDATE",
                                        (digest,)).fetchone()
        if prior_type != kind:
            # Preserve an existing curated or uploaded document and its already-committed postings.
            return str(doc_id), "EXISTING_" + prior_type
        if kind == "SCHEME_ARTICLE":
            state = db.execute("SELECT status FROM index_job WHERE doc_id=%s", (doc_id,)).fetchone()
            store_article(root, digest, payload, pending=state is None or state[0] == "QUEUED")
        else:
            store_pdf(root, digest, payload)
        db.execute("""
            INSERT INTO corpus_source
            (doc_id,paths,regions,displayed_title,department,category,references_found,page_count,export_date,evidence)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT(doc_id) DO UPDATE SET paths=excluded.paths, regions=excluded.regions,
                displayed_title=excluded.displayed_title, department=excluded.department, category=excluded.category,
                references_found=excluded.references_found, page_count=excluded.page_count,
                export_date=excluded.export_date, evidence=excluded.evidence
        """, (doc_id,item["paths"],item["regions"],item.get("displayed_title"),item.get("department"),
              item.get("category"),item["references"],item.get("page_count"),item.get("export_date"),
              json.dumps(item["evidence"])))
        db.execute("INSERT INTO ingestion_job(doc_id) VALUES (%s) ON CONFLICT(doc_id) DO NOTHING", (doc_id,))
    return str(doc_id), "QUEUED" if created else "ALREADY_IMPORTED"


def preprocess(task):
    root, item = task
    path = root / item["paths"][0]
    try:
        payload = read_input(path)
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError("CONTENT_CHANGED")
        if item["source_type"] == "SCHEME_ARTICLE":
            return describe_article(item, payload), item
        describe_pdf(item, path, payload)
        return None, item
    except (OSError, UnicodeError, ValueError) as error:
        item.update(status="REJECTED", reason=getattr(error, "code", None) or str(error))
        return None, item


def import_selected(db, root, storage, selected, workers):
    links = scheme_links(db)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        batch_size = workers * 2
        for start in range(0, len(selected), batch_size):
            group = selected[start:start + batch_size]
            tasks = [(root, item) for item in group]
            for original, (payload, item) in zip(group, pool.map(preprocess, tasks), strict=True):
                if item.get("status") != "REJECTED":
                    try:
                        if payload is None:
                            payload = read_input(root / item["paths"][0])
                            if hashlib.sha256(payload).hexdigest() != item["sha256"]:
                                raise ValueError("CONTENT_CHANGED")
                        item["doc_id"], item["status"] = register(db, storage, item, payload, links)
                    except (OSError, ValueError, psycopg.IntegrityError) as error:
                        item.update(status="REJECTED", reason=getattr(error, "sqlstate", None) or str(error))
                original.update(item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("discover", "sample", "full"))
    parser.add_argument("--raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--storage", type=Path, default=Path(os.getenv("PDF_ROOT", "data/pdfs")))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    root = args.raw.resolve()
    records, skipped = discover(root)
    discovered = {}
    for _, kind in TYPES.values():
        paths = [item["paths"] for item in records if item["source_type"] == kind]
        discovered[kind] = {"files": sum(map(len, paths)), "unique": len(paths),
                            "duplicates": sum(len(group) - 1 for group in paths)}
    if args.mode != "discover":
        url = os.environ["DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as db:
            selected = sample(records, args.sample_size) if args.mode == "sample" else records
            import_selected(db, root, args.storage.resolve(), selected, args.workers)
    summary = {"mode": args.mode, "discovery": discovered, "skipped": skipped,
               "counts": dict(Counter(item.get("status", "DISCOVERED") for item in records))}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({**summary, "records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
