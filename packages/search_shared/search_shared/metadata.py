"""E3's read-only SQL interface for E2. Inject a PostgreSQL DB-API connection."""

import unicodedata
from uuid import UUID

FILTER_FIELDS = ("state", "department", "category")


def normalize_filter(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()) or None


class MetadataRepository:
    def __init__(self, connection, committed_doc_ids):
        """Bind metadata reads to the inventory of one validated E1 snapshot."""
        self.connection = connection
        self.committed_doc_ids = frozenset(str(UUID(str(value))) for value in committed_doc_ids)

    def get_matching_docs(self, filters: dict) -> set[str]:
        if not self.committed_doc_ids:
            return set()
        committed = list(self.committed_doc_ids)
        active = {field: value for field in FILTER_FIELDS if (value := normalize_filter(filters.get(field))) is not None}
        selected = dict.fromkeys(FILTER_FIELDS)
        with self.connection.cursor() as cursor:
            if active:
                cursor.execute(
                    """SELECT DISTINCT region, department, category FROM e3_indexed_document
                       LEFT JOIN LATERAL unnest(regions) AS region ON true
                       WHERE doc_id = ANY(%s::uuid[])""", (committed,),
                )
                acceptable = {field: set() for field in active}
                for row in cursor.fetchall():
                    for field, actual in zip(FILTER_FIELDS, row, strict=True):
                        if field in active and normalize_filter(actual) == active[field]:
                            acceptable[field].add(actual)
                if any(not values for values in acceptable.values()):
                    return set()
                selected.update({field: sorted(values) for field, values in acceptable.items()})
            cursor.execute(
                """
                SELECT doc_id FROM e3_indexed_document
                WHERE doc_id = ANY(%(committed)s::uuid[])
                  AND (%(state)s::text[] IS NULL OR regions && %(state)s::text[])
                  AND (%(department)s::text[] IS NULL OR department = ANY(%(department)s::text[]))
                  AND (%(category)s::text[] IS NULL OR category = ANY(%(category)s::text[]))
                """,
                {"committed": committed, **selected},
            )
            return {str(row[0]) for row in cursor.fetchall()}

    def get_metadata(self, doc_ids: list[str]) -> dict[str, dict]:
        eligible = self.committed_doc_ids.intersection(str(UUID(str(value))) for value in doc_ids)
        if not eligible:
            return {}
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT doc_id, scheme_id, title, department, issue_date, source_type, version_status, superseded_by
                FROM e3_indexed_document WHERE doc_id = ANY(%s::uuid[])
                """,
                (list(eligible),),
            )
            return {
                str(doc_id): {
                    "scheme_id": str(scheme_id) if scheme_id else None,
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
