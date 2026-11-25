"""Poll the durable queue and delegate each drain to E1."""

import json
import logging
import os
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from engines.e1.jobs import JobStoreError
from engines.e1.parallel import PipelineError, run_parallel


def main():
    load_dotenv()
    url = os.environ["E1_DATABASE_URL"]
    if not url:
        raise SystemExit("Set E1_DATABASE_URL to the restricted indexer login")
    index = Path(os.getenv("INDEX_ROOT", "data/index"))
    pdfs = Path(os.getenv("PDF_ROOT", "data/pdfs"))
    workers = int(os.getenv("E1_WORKERS", "2"))
    if not 1 <= workers <= 8:
        raise SystemExit("E1_WORKERS must be between 1 and 8")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    while True:
        try:
            with psycopg.connect(url, connect_timeout=2, autocommit=True) as db:
                pending = db.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM index_job WHERE
                        (status='QUEUED' AND available_at<=now() AND attempts<max_attempts)
                        OR status IN ('PARSING','COMMITTING')
                    )
                """).fetchone()[0]
            if pending or not (index / "manifest.json").is_file():
                run_parallel(index, workers=workers, database_url=url, pdf_root=pdfs)
        except (psycopg.Error, JobStoreError, OSError, PipelineError) as error:
            print(json.dumps({"event": "INDEXER_POLL_FAILED", "kind": type(error).__name__}), flush=True)
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
