"""Run the FR-01 reference indexer on a verified E3 export."""

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from .reference import load_pdf_jobs, load_scheme_records
from .snapshot import index_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-jobs", type=Path, help="JSON export of E3-verified PDF jobs")
    parser.add_argument("--pdf-root", type=Path, default=os.getenv("PDF_ROOT"))
    parser.add_argument("--schemes", type=Path, help="CSV of E3-imported scheme records")
    parser.add_argument("--index-root", type=Path, default=os.getenv("INDEX_ROOT"))
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args(argv)
    if args.index_root is None or (args.pdf_jobs is None and args.schemes is None):
        parser.error("Supply --index-root (or INDEX_ROOT) and at least one input")
    if args.pdf_jobs is not None and args.pdf_root is None:
        parser.error("--pdf-jobs requires --pdf-root or PDF_ROOT")
    if args.max_tokens < 1:
        parser.error("--max-tokens must be positive")

    try:
        sources = load_pdf_jobs(args.pdf_jobs, args.pdf_root) if args.pdf_jobs is not None else []
        if args.schemes is not None:
            sources.extend(load_scheme_records(args.schemes))
        report = index_sources(args.index_root, sources, args.max_tokens)
    except (OSError, ValueError, csv.Error) as error:
        print(f"E1 could not build the index: {error}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2))
    return int(any(document["status"] == "FAILED" for document in report["documents"]))


if __name__ == "__main__":
    raise SystemExit(main())
