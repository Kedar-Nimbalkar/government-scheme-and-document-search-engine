"""Measure the sequential baseline and spawn pipeline on one synthetic PDF corpus."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid5

from pypdf import PdfReader, PdfWriter
from search_shared.index_reader import read_snapshot

from .parallel import run_parallel
from .reference import Source
from .snapshot import index_sources, merge_segments


def fingerprint(root):
    manifest, segments = read_snapshot(root)
    merged = merge_segments(segments)
    passages = {row["passage_id"]: (row["doc_id"], row["page_no"], row["text"], row["length"], row["token_spans"])
                for row in merged["passages"]}
    postings = {
        term: sorted((passages[row["passage_id"]][:2], row["positions"]) for row in rows)
        for term, rows in merged["postings"].items()
    }
    encoded = json.dumps([manifest["stats"], sorted(passages.values()), postings], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=48)
    parser.add_argument("--pages", type=int, default=24)
    args = parser.parse_args()
    if min(args.documents, args.pages) < 1:
        parser.error("Corpus dimensions must be positive")
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "e1-synthetic" / "digital-and-mixed.pdf"
    with TemporaryDirectory(prefix="team06-e1-benchmark-") as working:
        root = Path(working)
        document = root / "synthetic.pdf"
        source = PdfReader(fixture)
        writer = PdfWriter()
        writer.add_metadata({"/Title": "Synthetic E1 benchmark - not an official document"})
        for number in range(args.pages):
            writer.add_page(source.pages[(0, 2)[number % 2]])
        writer.write(document)
        jobs = [Source(str(uuid5(NAMESPACE_URL, f"team06-synthetic/{number}")), "PDF",
                       job_id=str(uuid5(NAMESPACE_URL, f"team06-synthetic-job/{number}")), pdf_path=document)
                for number in range(args.documents)]
        runs = []
        for workers in (0, 2, 4):
            path = root / f"index-{workers}"
            started = time.perf_counter()
            report = run_parallel(path, jobs, workers=workers) if workers else index_sources(path, jobs)
            wall = time.perf_counter() - started
            indexed = sum(row["status"] == "INDEXED" for row in report["documents"])
            runs.append({
                "mode": f"{workers} workers" if workers else "sequential",
                "wall_seconds": round(wall, 4), "docs_per_second": round(indexed / wall, 3),
                "indexed": indexed, "failures": len(jobs) - indexed,
                "supervisor_pid": report.get("supervisor_pid", os.getpid()),
                "writer_pid": report.get("writer_pid", os.getpid()),
                "worker_pids": report.get("worker_pids", []),
                "queue_peak": report.get("queue_peak", {"task_per_worker": 0, "result": 0, "writer": 0, "ack": 0}),
                "fingerprint": fingerprint(path),
            })
        if len({run["fingerprint"] for run in runs}) != 1:
            raise RuntimeError("The benchmark runs did not index equivalent content")
        print(json.dumps({"synthetic_documents": args.documents, "pages_per_document": args.pages, "runs": runs}, indent=2))


if __name__ == "__main__":
    main()
