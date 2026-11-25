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
            if row["source_type"] != "PDF":
                raise ValueError("Expected an E3 PDF job")
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
            sources.append(Source(
                doc_id=_uuid(row["doc_id"]),
                source_type="PDF",
                job_id=_uuid(row["job_id"]),
                pdf_path=pdf_path,
                scheme_id=_uuid(scheme_id) if scheme_id is not None else None,
            ))
        except (KeyError, AttributeError, TypeError, ValueError, OSError, RuntimeError) as error:
            raise InputError(f"Invalid PDF job row {number}: {error}") from error
    return sources


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


def build_segment(sources: list[Source], committed: set[str], next_id: int, max_tokens: int):
    """Build one segment and one per-source report, without performing index I/O."""
    passages = []
    postings = defaultdict(list)
    indexed = set()
    reports = []
    for source in sorted(sources, key=lambda item: item.doc_id):
        report = {"doc_id": source.doc_id, "source_type": source.source_type}
        if source.job_id is not None:
            report["job_id"] = source.job_id
        if source.scheme_id is not None:
            report["scheme_id"] = source.scheme_id
        reports.append(report)
        if source.doc_id in committed or source.doc_id in indexed:
            report["status"] = "SKIPPED_DUPLICATE"
            continue

        omitted = []
        if source.source_type == "PDF":
            try:
                extracted = extract_pdf(source.pdf_path)
            except PdfExtractionError as error:
                report.update(status="FAILED", reason=error.code, omitted_pages=list(error.omitted_pages))
                continue
            pages = extracted.pages
            omitted.extend(extracted.omitted_pages)
        else:
            pages = ((None, source.text),)

        before = len(passages)
        for page, text in pages:
            page_before = len(passages)
            for passage_text, tokens in split_passages(text, max_tokens):
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
                    "passage_id": passage_id, "doc_id": source.doc_id, "page_no": page,
                    "text": passage_text, "length": len(tokens), "token_spans": spans,
                })
            if page is not None and len(passages) == page_before:
                omitted.append(page)

        report["omitted_pages"] = sorted(omitted)
        if len(passages) == before:
            report.update(status="FAILED", reason="NO_SEARCHABLE_TOKENS")
        else:
            indexed.add(source.doc_id)
            report.update(status="INDEXED", passages=len(passages) - before)

    dictionary = sorted(postings)
    return {
        "schema_version": SCHEMA_VERSION,
        "tokenizer_version": TOKENIZER_VERSION,
        "dictionary": dictionary,
        "postings": {term: postings[term] for term in dictionary},
        "passages": passages,
        "df": {term: len(postings[term]) for term in dictionary},
    }, indexed, next_id, reports
