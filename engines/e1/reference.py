"""Sequential FR-01 parser and positional index construction."""

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from uuid import UUID

from search_shared import SCHEMA_VERSION
from search_shared.index_reader import encode_passage_id
from search_shared.tokenizer import TOKENIZER_VERSION, tokenize_with_spans

from .pdf import PdfExtractionError, extract_pdf
from .source_cleaning import clean_snapshot_page

PDF_TYPES = {"PDF", "SCHEME_PDF", "OFFICIAL_PDF"}
FILE_TYPES = PDF_TYPES | {"SCHEME_ARTICLE"}


class InputError(ValueError):
    """The supplied import manifest or CSV does not meet E1's input contract."""


@dataclass(frozen=True)
class Source:
    doc_id: str
    source_type: str
    job_id: str | None = None
    pdf_path: Path | None = None
    scheme_id: str | None = None
    text: str | None = None
    article_path: Path | None = None


def _uuid(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Expected a canonical lowercase UUID")
    return value


def load_pdf_jobs(path: Path, pdf_root: Path) -> list[Source]:
    """Read the stable projection of E3-verified PDF job rows."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise InputError("The PDF job export must be a JSON array")
    root = pdf_root.resolve()
    sources = []
    for number, row in enumerate(rows, 1):
        try:
            sources.append(pdf_source(row, root))
        except (KeyError, AttributeError, TypeError, ValueError, OSError, RuntimeError) as error:
            raise InputError(f"Invalid PDF job row {number}: {error}") from error
    return sources


def pdf_source(row: dict, root: Path) -> Source:
    return file_source(row, root)


def file_source(row: dict, root: Path) -> Source:
    kind = row["source_type"]
    if kind not in FILE_TYPES:
        raise ValueError("Expected a file-backed E3 job")
    relative = row["file_path"]
    if not isinstance(relative, str) or not relative:
        raise ValueError("Expected a relative PDF path")
    windows_path = PureWindowsPath(relative)
    if windows_path.drive or windows_path.root or any(
        part == ".." or ":" in part for part in windows_path.parts
    ):
        raise ValueError("PDF path must be relative to PDF_ROOT")
    pdf_path = root.joinpath(*windows_path.parts).resolve()
    if pdf_path == root or not pdf_path.is_relative_to(root):
        raise ValueError("PDF path escapes PDF_ROOT")
    scheme_id = row.get("scheme_id")
    return Source(
        doc_id=_uuid(row["doc_id"]), source_type=kind, job_id=_uuid(row["job_id"]),
        pdf_path=pdf_path if kind in PDF_TYPES else None,
        article_path=pdf_path if kind == "SCHEME_ARTICLE" else None,
        scheme_id=_uuid(scheme_id) if scheme_id is not None else None,
    )


def load_scheme_records(path: Path) -> list[Source]:
    """Read searchable text and stable IDs assigned by the E3 scheme import."""
    sources = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = csv.DictReader(source, strict=True)
        if not {"doc_id", "scheme_id", "text"}.issubset(rows.fieldnames or ()):
            raise InputError("Scheme CSV must have doc_id, scheme_id, and text columns")
        for row in rows:
            try:
                if None in row or row["text"] is None:
                    raise ValueError("Invalid CSV column count")
                sources.append(Source(
                    doc_id=_uuid(row["doc_id"]),
                    source_type="scheme_record",
                    scheme_id=_uuid(row["scheme_id"]),
                    text=row["text"],
                ))
            except ValueError as error:
                raise InputError(f"Invalid scheme record ending at line {rows.line_num}: {error}") from error
    return sources


def split_passages(text: str, max_tokens: int):
    """Yield verbatim text slices, terms, and passage-relative UTF-16 spans."""
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    tokens = tokenize_with_spans(text)
    if not tokens:
        return

    utf16 = 0
    boundaries = {0: 0}
    for offset, character in enumerate(text, 1):
        utf16 += 2 if ord(character) > 0xFFFF else 1
        boundaries[utf16] = offset

    first = start = 0
    while first < len(tokens):
        last = min(first + max_tokens, len(tokens))
        # A compatibility character may expand into multiple tokens. Keep
        # overlapping source spans together so the original text stays whole.
        while last < len(tokens) and tokens[last][1]["start"] < tokens[last - 1][1]["end"]:
            last += 1
        end = tokens[last][1]["start"] if last < len(tokens) else utf16
        yield text[boundaries[start]:boundaries[end]], [
            (term, {"start": span["start"] - start, "end": span["end"] - start})
            for term, span in tokens[first:last]
        ]
        first, start = last, end


def source_report(source: Source) -> dict:
    report = {"doc_id": source.doc_id, "source_type": source.source_type}
    if source.job_id is not None:
        report["job_id"] = source.job_id
    if source.scheme_id is not None:
        report["scheme_id"] = source.scheme_id
    return report


def parse_source(source: Source, max_tokens: int, emit, *, max_text_bytes: int | None = None) -> dict:
    report = source_report(source)
    omitted = []
    if source.source_type in PDF_TYPES:
        try:
            extracted = extract_pdf(source.pdf_path, max_text_bytes=max_text_bytes)
        except PdfExtractionError as error:
            return {**report, "status": "FAILED", "reason": error.code, "omitted_pages": list(error.omitted_pages)}
        pages = extracted.pages
        omitted.extend(extracted.omitted_pages)
    elif source.source_type == "SCHEME_ARTICLE":
        try:
            text = source.article_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            return {**report, "status": "FAILED", "reason": "ARTICLE_UNREADABLE"}
        pages = ((None, text),)
    else:
        pages = ((None, source.text),)

    count = 0
    for page, text in pages:
        before = count
        if source.source_type == "SCHEME_PDF":
            text = clean_snapshot_page(text)
        blocks = text.split("\n\n") if source.source_type in {"SCHEME_ARTICLE", "SCHEME_PDF"} else (text,)
        for block in blocks:
            for passage_text, tokens in split_passages(block, max_tokens):
                emit((page, passage_text, tokens))
                count += 1
        if page is not None and count == before:
            omitted.append(page)
    report["omitted_pages"] = sorted(omitted)
    if count:
        report.update(status="INDEXED", passages=count)
    else:
        report.update(status="FAILED", reason="NO_SEARCHABLE_TOKENS")
    return report


def compile_segment(documents, next_id: int):
    """Assign persistent IDs and compile complete parsed documents."""
    passages = []
    postings = defaultdict(list)
    for doc_id, records in documents:
        for page, text, tokens in records:
            passage_id = encode_passage_id(next_id)
            next_id += 1
            positions = defaultdict(list)
            spans = []
            for position, (term, span) in enumerate(tokens):
                positions[term].append(position)
                spans.append(span)
            for term, offsets in positions.items():
                postings[term].append({"passage_id": passage_id, "positions": offsets})
            passages.append({
                "passage_id": passage_id, "doc_id": doc_id, "page_no": page,
                "text": text, "length": len(tokens), "token_spans": spans,
            })

    dictionary = sorted(postings)
    return {
        "schema_version": SCHEMA_VERSION,
        "tokenizer_version": TOKENIZER_VERSION,
        "dictionary": dictionary,
        "postings": {term: postings[term] for term in dictionary},
        "passages": passages,
        "df": {term: len(postings[term]) for term in dictionary},
    }, next_id


def build_segment(sources: list[Source], committed: set[str], next_id: int, max_tokens: int):
    documents = []
    indexed = set()
    reports = []
    for source in sorted(sources, key=lambda item: item.doc_id):
        if source.doc_id in committed or source.doc_id in indexed:
            reports.append({**source_report(source), "status": "SKIPPED_DUPLICATE"})
            continue
        records = []
        report = parse_source(source, max_tokens, records.append)
        reports.append(report)
        if report["status"] == "INDEXED":
            documents.append((source.doc_id, records))
            indexed.add(source.doc_id)
    segment, next_id = compile_segment(documents, next_id)
    return segment, indexed, next_id, reports
