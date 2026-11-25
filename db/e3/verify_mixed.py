"""Verify a mixed snapshot and time public E4 search requests."""

import argparse
import json
import statistics
from io import BytesIO
from pathlib import Path
from time import perf_counter, sleep
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from pypdf import PdfReader
from search_shared.index_reader import read_snapshot
from search_shared.tokenizer import tokenize_with_spans

from engines.e1.source_cleaning import clean_snapshot_page

QUERIES = ("Scholarship", "Pension", "Housing", "Financial assistance", "Handloom",
           "Atal Amrit Abhiyan", "ARAVANAIPPU", '"capital investment subsidy"',
           "scholarship AND education", "pension OR retirement")
REGIONS = ("Karnataka", "Andhra Pradesh", "Assam", "Puducherry", "Central")


def get(url):
    with urlopen(url, timeout=12) as response:
        return response.read()


def json_at(base, path):
    return json.loads(get(base + path))


def search(base, query, **filters):
    start = perf_counter()
    data = json_at(base, "/api/search?" + urlencode({"q": query, **filters}))
    duration = round((perf_counter() - start) * 1000, 2)
    if "state" in filters:
        assert all(filters["state"] in hit["regions"] for hit in data["results"]), filters
    assert all(hit["page_no"] is None for hit in data["results"] if hit["source_type"] == "SCHEME_ARTICLE")
    return data, duration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:5173")
    parser.add_argument("--index-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    filters = json_at(args.base, "/api/filters")
    cold_start = perf_counter()
    retries = 0
    for attempt in range(6):
        try:
            search(args.base, "scholarship")
            break
        except HTTPError as error:
            if error.code != 504 or attempt == 5:
                raise
            retries += 1
            sleep(1)
    warmup_ms = round((perf_counter() - cold_start) * 1000, 2)

    results = []
    examples = {}
    for query, selected in [(q, {}) for q in QUERIES] + [("assistance OR scholarship", {"state": s}) for s in REGIONS]:
        data, duration = search(args.base, query, k=50, **selected)
        results.append({"query": query, **selected, "total": data["total"], "latency_ms": duration,
                        "top_title": data["results"][0]["title"] if data["results"] else None})
        for hit in data["results"]:
            if hit["source_type"] in {"SCHEME_ARTICLE", "SCHEME_PDF"} and hit["match_spans"]:
                examples.setdefault(hit["source_type"], hit)
    assert set(examples) == {"SCHEME_ARTICLE", "SCHEME_PDF"}, "Both corpus types must be discoverable"
    article = examples["SCHEME_ARTICLE"]
    text = json_at(args.base, "/api/documents/" + article["doc_id"])["article_text"]
    assert text and not article["pdf_url"]
    pdf = examples["SCHEME_PDF"]
    original = get(args.base + pdf["pdf_url"])
    pages = PdfReader(BytesIO(original)).pages
    assert original.startswith(b"%PDF-") and 1 <= pdf["page_no"] <= len(pages)
    page_text = clean_snapshot_page(pages[pdf["page_no"] - 1].extract_text())
    page_terms = {term for term, _ in tokenize_with_spans(page_text)}
    assert page_terms.intersection(pdf["matched_terms"]), "Matched terms must occur on the physical PDF page"
    assert json_at(args.base, "/api/autocomplete?prefix=sch")["suggestions"]
    manifest, segments = read_snapshot(args.index_root)
    terms = {term for segment in segments for term in segment["dictionary"]}
    durations = [item["latency_ms"] for item in results]
    report = {
        "generation": manifest["generation"], "indexed_documents": len(manifest["committed_doc_ids"]),
        "passages": manifest["stats"]["N"], "total_tokens": manifest["stats"]["total_tokens"],
        "vocabulary": len(terms), "live_segment_bytes": sum((args.index_root / item["file"]).stat().st_size
                                                         for item in manifest["segments"]),
        "filter_options": {key: len(values) for key, values in filters.items()},
        "queries": results, "warmup_ms": warmup_ms, "warmup_retries": retries, "median_ms": statistics.median(durations), "max_ms": max(durations),
        "article": {key: article[key] for key in ("title", "doc_id", "page_no", "match_spans")},
        "pdf": {key: pdf[key] for key in ("title", "doc_id", "page_no", "match_spans")},
    }
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
