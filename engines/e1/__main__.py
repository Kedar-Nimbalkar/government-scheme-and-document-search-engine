"""Run the FR-01 reference indexer on a verified E3 export."""

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path

from .jobs import JobStoreError
from .parallel import PipelineError, run_parallel
from .processes import Limits
from .reference import load_pdf_jobs, load_scheme_records
from .snapshot import index_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-jobs", type=Path, help="JSON export of E3-verified PDF jobs")
    parser.add_argument("--pdf-root", type=Path, default=os.getenv("PDF_ROOT"))
    parser.add_argument("--schemes", type=Path, help="CSV of E3-imported scheme records")
    parser.add_argument("--index-root", type=Path, default=os.getenv("INDEX_ROOT"))
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--workers", type=int, help="Spawn this many parsers and one separate writer")
    parser.add_argument("--from-database", action="store_true", help="Drain E3 jobs using DATABASE_URL")
    parser.add_argument("--compact", action="store_true", help="Heap-merge the committed segments after the run")
    parser.add_argument("--queue-items", type=int, default=Limits.queue_items)
    parser.add_argument("--chunk-bytes", type=int, default=Limits.chunk_bytes)
    parser.add_argument("--staged-bytes", type=int, default=Limits.staged_bytes)
    parser.add_argument("--input-bytes", type=int, default=Limits.input_bytes)
    parser.add_argument("--task-bytes", type=int, default=Limits.task_bytes)
    parser.add_argument("--worker-timeout", type=float, default=Limits.timeout_seconds)
    parser.add_argument("--lease-seconds", type=int, default=Limits.lease_seconds)
    parser.add_argument("--retry-seconds", type=int, default=Limits.retry_seconds)
    args = parser.parse_args(argv)
    if args.index_root is None or not (args.pdf_jobs or args.schemes or args.from_database or args.compact):
        parser.error("Supply --index-root (or INDEX_ROOT) and an input mode or --compact")
    if (args.pdf_jobs is not None or args.from_database) and args.pdf_root is None:
        parser.error("The selected PDF input mode requires --pdf-root or PDF_ROOT")
    database_url = os.getenv("DATABASE_URL") if args.from_database else None
    if args.from_database and (not database_url or args.pdf_jobs):
        parser.error("--from-database requires DATABASE_URL and cannot be combined with --pdf-jobs")
    if args.max_tokens < 1 or (args.workers is not None and args.workers < 1):
        parser.error("--max-tokens and --workers must be positive")

    try:
        sources = load_pdf_jobs(args.pdf_jobs, args.pdf_root) if args.pdf_jobs is not None else []
        if args.schemes is not None:
            sources.extend(load_scheme_records(args.schemes))
        if args.workers or args.from_database or args.compact:
            logging.basicConfig(level=logging.INFO, format="%(message)s")
            report = run_parallel(
                args.index_root, sources, workers=args.workers or 2, max_tokens=args.max_tokens,
                database_url=database_url, pdf_root=args.pdf_root, compact=args.compact,
                limits=Limits(
                    queue_items=args.queue_items, chunk_bytes=args.chunk_bytes, staged_bytes=args.staged_bytes,
                    input_bytes=args.input_bytes, task_bytes=args.task_bytes, timeout_seconds=args.worker_timeout,
                    lease_seconds=args.lease_seconds, retry_seconds=args.retry_seconds,
                ),
            )
        else:
            report = index_sources(args.index_root, sources, args.max_tokens)
    except (OSError, ValueError, csv.Error, JobStoreError, PipelineError, ImportError) as error:
        if args.from_database and not isinstance(error, PipelineError):
            print(f"E1 durable run failed ({type(error).__name__})", file=sys.stderr)
            return 2
        print(f"E1 could not build the index: {error}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2))
    return int(any(document["status"] in {"FAILED", "LEASE_LOST"} for document in report["documents"]))


if __name__ == "__main__":
    raise SystemExit(main())
