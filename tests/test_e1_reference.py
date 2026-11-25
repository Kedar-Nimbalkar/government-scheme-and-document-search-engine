import json
from dataclasses import replace
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from search_shared.index_reader import read_snapshot
from search_shared.tokenizer import tokenize_with_spans

from engines.e1.__main__ import main
from engines.e1.pdf import (
    CorruptPdfError,
    EncryptedPdfError,
    NoTextPdfError,
    UnreadablePdfError,
    extract_pdf,
)
from engines.e1.reference import InputError, load_pdf_jobs, load_scheme_records, split_passages
from engines.e1.snapshot import index_sources

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "e1-synthetic"


@pytest.fixture
def sources():
    return load_pdf_jobs(FIXTURE / "pdf-jobs.json", FIXTURE) + load_scheme_records(FIXTURE / "schemes.csv")


def test_cli_snapshot_matches_reader_fixture(tmp_path, capsys):
    assert main([
        "--pdf-root", str(FIXTURE), "--pdf-jobs", str(FIXTURE / "pdf-jobs.json"),
        "--schemes", str(FIXTURE / "schemes.csv"), "--index-root", str(tmp_path),
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == json.loads((FIXTURE / "extraction-report.json").read_text(encoding="utf-8"))
    assert read_snapshot(tmp_path) == read_snapshot(FIXTURE / "index")
    for expected in (FIXTURE / "index").glob("*.json"):
        assert (tmp_path / expected.name).read_bytes() == expected.read_bytes()


def test_postings_phrase_positions_language_and_page_boundaries(tmp_path, sources):
    index_sources(tmp_path, sources)
    manifest, [segment] = read_snapshot(tmp_path)
    first, third, scheme = segment["passages"]
    assert [first["page_no"], third["page_no"], scheme["page_no"]] == [1, 3, None]
    assert [first["length"], third["length"], scheme["length"]] == [5, 3, 5]
    assert scheme["doc_id"] == sources[1].doc_id
    assert manifest["stats"] == {"N": 3, "total_tokens": 13, "avgdl": 13 / 3}
    assert segment["dictionary"] == sorted(segment["postings"])
    assert segment["df"]["grant"] == 3

    def offsets(term):
        return {row["passage_id"]: row["positions"] for row in segment["postings"][term]}

    grant, housing = offsets("grant"), offsets("housing")
    assert grant[first["passage_id"]] == [2, 3]
    assert housing[first["passage_id"]] == [4]
    # Exact phrase "housing grant" is contiguous on page 3 and in the CSV,
    # while page 1 has the two terms in the reverse order.
    assert {pid for pid in housing if set(grant[pid]) & {offset + 1 for offset in housing[pid]}} == {
        third["passage_id"], scheme["passage_id"],
    }
    assert offsets("ಕನ್ನಡ") == {scheme["passage_id"]: [1]}
    assert offsets("हिंदी") == {scheme["passage_id"]: [2]}


def test_mixed_pdf_verbatim_text_and_omitted_pages(tmp_path, sources):
    report = index_sources(tmp_path, sources[:1], max_tokens=2)
    _, [segment] = read_snapshot(tmp_path)
    with (FIXTURE / "digital-and-mixed.pdf").open("rb") as pdf:
        reader = PdfReader(pdf, strict=True)
        for number in (3, 4):
            assert b"INLINE IMAGE" in [operator for _, operator in reader.pages[number - 1].get_contents().operations]
        for number in (1, 3):
            passages = [item for item in segment["passages"] if item["page_no"] == number]
            assert len(passages) > 1
            assert "".join(item["text"] for item in passages) == reader.pages[number - 1].extract_text()
            assert all(item["length"] <= 2 for item in passages)
    assert report["documents"][0]["omitted_pages"] == [2, 4]


def test_source_offsets_rebase_after_split():
    text = " 🏠 Cafe\u0301   हिंदी \nStraße "
    parts = list(split_passages(text, max_tokens=1))
    assert "".join(part for part, _ in parts) == text
    assert [tokens for _, tokens in parts] == [tokenize_with_spans(part) for part, _ in parts]
    assert parts[0][1] == [("café", {"start": 4, "end": 9})]


def test_typed_pdf_errors_and_batch_isolation(tmp_path, sources):
    missing = tmp_path / "missing.pdf"
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"%PDF-1.4\n1 0 obj\n<<")
    encrypted = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.encrypt("synthetic-password")
    writer.write(encrypted)
    image_only = FIXTURE / "image-only.pdf"
    for path, error in [
        (missing, UnreadablePdfError), (corrupt, CorruptPdfError),
        (encrypted, EncryptedPdfError), (image_only, NoTextPdfError),
    ]:
        with pytest.raises(error):
            extract_pdf(path)
    invalid = replace(sources[0], doc_id="00000000-0000-4000-8000-000000000001", pdf_path=corrupt)
    image = replace(sources[0], doc_id="00000000-0000-4000-8000-000000000002", pdf_path=image_only)
    report = index_sources(tmp_path / "index", [invalid, image, *sources])
    assert [(row["reason"], row["omitted_pages"]) for row in report["documents"][:2]] == [
        ("PDF_CORRUPT", []), ("PDF_NO_TEXT", [1]),
    ]
    manifest, _ = read_snapshot(tmp_path / "index")
    assert manifest["committed_doc_ids"] == [source.doc_id for source in sources]


def test_writer_resumes_ids_and_deduplicates(tmp_path, sources):
    first = index_sources(tmp_path, [sources[0], sources[0]])
    assert [row["status"] for row in first["documents"]] == ["INDEXED", "SKIPPED_DUPLICATE"]
    second = index_sources(tmp_path, sources)
    assert [row["status"] for row in second["documents"]] == ["SKIPPED_DUPLICATE", "INDEXED"]
    manifest, [pdf, scheme] = read_snapshot(tmp_path)
    assert manifest["generation"] == 2 and manifest["next_passage_id"] == 4
    assert [entry["passage_id"] for entry in pdf["passages"] + scheme["passages"]] == [1, 2, 3]
    before = (tmp_path / "manifest.json").read_bytes()
    assert index_sources(tmp_path, sources)["generation"] == 2
    assert (tmp_path / "manifest.json").read_bytes() == before


def test_reject_job_paths_outside_verified_root(tmp_path):
    [job] = json.loads((FIXTURE / "pdf-jobs.json").read_text(encoding="utf-8"))
    export = tmp_path / "jobs.json"
    for candidate in ["../unverified.pdf", "C:\\outside.pdf", "/unverified.pdf"]:
        export.write_text(json.dumps([{**job, "file_path": candidate}]), encoding="utf-8")
        with pytest.raises(InputError, match="relative to PDF_ROOT"):
            load_pdf_jobs(export, FIXTURE)
