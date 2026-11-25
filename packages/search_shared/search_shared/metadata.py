"""E3's read-only SQL interface for E2. Inject a PostgreSQL DB-API connection."""

from uuid import UUID


class MetadataRepository:
    def __init__(self, connection):
        self.connection = connection

    def get_matching_docs(self, filters: dict) -> set[str]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.doc_id
                FROM document d
                JOIN scheme s USING (scheme_id)
                JOIN department p USING (dept_id)
                WHERE (%(state)s::text IS NULL OR s.state = %(state)s)
                  AND (%(department)s::text IS NULL OR p.dept_name = %(department)s)
                  AND (%(category)s::text IS NULL OR s.category = %(category)s)
                """,
                {name: filters.get(name) for name in ("state", "department", "category")},
            )
            return {str(row[0]) for row in cursor.fetchall()}

    def get_metadata(self, doc_ids: list[str]) -> dict[str, dict]:
        if not doc_ids:
            return {}
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.doc_id, d.scheme_id, d.title, p.dept_name AS department,
                       d.issue_date, d.source_type, d.version_status, d.superseded_by
                FROM document d
                JOIN scheme s USING (scheme_id)
                JOIN department p USING (dept_id)
                WHERE d.doc_id = ANY(%s::uuid[])
                """,
                ([str(UUID(value)) for value in doc_ids],),
            )
            return {
                str(doc_id): {
                    "scheme_id": str(scheme_id),
                    "title": title,
                    "department": department,
                    "issue_date": issue_date.isoformat() if issue_date else None,
                    "source_type": source_type,
                    "version_status": version_status,
                    "superseded": None if version_status == "UNKNOWN" else version_status == "SUPERSEDED",
                    "current_doc_id": str(superseded_by) if version_status == "SUPERSEDED" and superseded_by else None,
                }
                for doc_id, scheme_id, title, department, issue_date, source_type, version_status, superseded_by
                in cursor.fetchall()
            }
