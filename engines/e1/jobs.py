"""Use the E3-owned prepared job SQL without changing the database contract."""

from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

JOBS_SQL = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "v2" / "jobs.sql"


class JobStoreError(RuntimeError):
    pass


class JobStore:
    def __init__(self, connection, lease_seconds: int, retry_seconds: int):
        self.connection = connection
        self.lease = timedelta(seconds=lease_seconds)
        self.retry = timedelta(seconds=retry_seconds)

    def _query(self, command: str, params=(), *, all_rows: bool = False):
        from psycopg import ClientCursor

        with ClientCursor(self.connection) as cursor:
            cursor.execute(command, params)
            return cursor.fetchall() if all_rows else cursor.fetchone()

    def claim(self):
        return self._query("EXECUTE claim_index_job(%s)", (self.lease,))

    def acquire_writer(self) -> bool:
        return self._query("EXECUTE acquire_index_writer")[0]

    def renew(self, job_id, lease_token):
        return self._query("EXECUTE renew_index_job(%s,%s,%s)", (job_id, lease_token, self.lease))

    def begin(self, job_id, lease_token):
        return self._query("EXECUTE begin_index_commit(%s,%s)", (job_id, lease_token))

    def finish(self, job_id, lease_token):
        return self._query("EXECUTE finish_index_commit(%s,%s)", (job_id, lease_token))

    def fail(self, job_id, lease_token, retryable, reason):
        return self._query(
            "EXECUTE fail_index_parse(%s,%s,%s,%s,%s)", (job_id, lease_token, retryable, reason, self.retry),
        )

    def reconcile(self, committed: set[str]):
        outcomes = []
        for job_id, doc_id in self._query("EXECUTE list_expired_index_jobs", all_rows=True):
            outcome = self._query(
                "EXECUTE reconcile_index_job(%s,%s,%s)", (job_id, str(doc_id) in committed, self.retry),
            )
            if outcome:
                outcomes.append({"job_id": str(job_id), "doc_id": str(doc_id), "status": outcome[1]})
        return outcomes

    def recovery_delay(self) -> float | None:
        row = self._query("EXECUTE next_index_recovery")
        return None if row[0] is None else max(0.0, float(row[0]))


@contextmanager
def open_jobs(database_url: str | None, lease_seconds: int, retry_seconds: int):
    if database_url is None:
        yield None
        return
    import psycopg

    try:
        with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as connection:
            connection.execute("SET statement_timeout = '10s'")
            connection.execute(JOBS_SQL.read_text(encoding="utf-8"), prepare=False)
            yield JobStore(connection, lease_seconds, retry_seconds)
    except psycopg.Error as error:
        raise JobStoreError("PostgreSQL job operation failed") from error
