"""Search a validated index while PostgreSQL supplies eligibility and metadata."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from math import isclose
from pathlib import Path
from threading import Lock

from search_shared.index_reader import encode_passage_id, passage_id_value, read_snapshot
from search_shared.tokenizer import tokenize

from .assistance import MAX_PREFIX, Trie, assist
from .budget import Budget, RequestTimeout
from .query import Atom, InvalidQuery, evaluate, parse, phrase_starts, sorted_merge, validate_page
from .ranking import BM25, TopK, inverse_document_frequency, snippet


class SearchUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    manifest: dict
    passages: dict
    ordered_ids: list[int]
    postings: dict
    frequency: dict
    trie: Trie

    @classmethod
    def load(cls, root: Path):
        manifest, segments = read_snapshot(root)
        passages = {}
        postings = defaultdict(dict)
        frequency = defaultdict(int)
        occurrences = defaultdict(int)
        for segment in segments:
            for passage in segment["passages"]:
                passages[passage_id_value(passage["passage_id"])] = passage
            for term, df in segment["df"].items():
                frequency[term] += df
            for term, rows in segment["postings"].items():
                for row in rows:
                    postings[term][passage_id_value(row["passage_id"])] = row["positions"]
                    occurrences[term] += len(row["positions"])
        stats = manifest["stats"]
        size = len(passages)
        tokens = sum(passage["length"] for passage in passages.values())
        if (stats["N"] != size or stats["total_tokens"] != tokens
                or not isclose(stats["avgdl"], tokens / size if size else 0)
                or any(p["length"] <= 0 or p["length"] != len(p["token_spans"]) for p in passages.values())
                or set(frequency) != set(postings)
                or any(not 0 < df <= size or df != len(postings[term]) for term, df in frequency.items())):
            raise ValueError("Inconsistent index statistics")
        return cls(manifest, passages, sorted(passages),
                   {term: dict(sorted(rows.items())) for term, rows in postings.items()}, dict(frequency), Trie(occurrences))


class SnapshotCache:
    def __init__(self, root: Path):
        self.root = root
        self._lock = Lock()
        self._key = self._snapshot = None

    def get(self, budget: Budget | None = None) -> Snapshot:
        locked = self._lock.acquire(timeout=budget.remaining()) if budget else self._lock.acquire()
        if not locked:
            raise RequestTimeout("Snapshot wait budget exhausted")
        try:
            try:
                stat = (self.root / "manifest.json").stat()
                key = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
                if key != self._key:
                    self._snapshot = Snapshot.load(self.root)
                    self._key = key
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                raise SearchUnavailable("Index snapshot is unavailable") from error
            if budget:
                budget.check()
            return self._snapshot
        finally:
            self._lock.release()


def atom_matches(atom: Atom, snapshot: Snapshot, eligible: list[int]) -> dict[int, list[int]]:
    postings = [snapshot.postings.get(term, {}) for term in atom.terms]
    candidates = sorted_merge(eligible, list(postings[0]), "AND")
    for rows in postings[1:]:
        candidates = sorted_merge(candidates, list(rows), "AND")
        if not candidates:
            return {}
    return {
        identifier: positions for identifier in candidates
        if (positions := phrase_starts([rows[identifier] for rows in postings]))
    }


class SearchEngine:
    def __init__(self, snapshots, metadata_context, ranking: BM25 = BM25()):
        self.snapshots = snapshots
        self.metadata_context = metadata_context
        self.ranking = ranking

    def autocomplete(self, prefix: str, budget: Budget | None = None) -> list[str]:
        if not isinstance(prefix, str) or not prefix.strip() or len(prefix) > MAX_PREFIX:
            raise InvalidQuery(f"Prefix must contain 1–{MAX_PREFIX} characters")
        tokens = tokenize(prefix)
        if not tokens:
            return []
        budget = budget or Budget(1)
        snapshot = self.snapshots.get(budget)
        return snapshot.trie.suggest(tokens[-1])

    def search(self, query, filters=None, k=10, page=1, budget: Budget | None = None):
        budget = budget or Budget(3)
        validate_page(k, page)
        parsed = parse(query)
        snapshot = self.snapshots.get(budget)
        parsed, suggestion = assist(query, parsed, snapshot.trie, budget.check)
        with self.metadata_context(snapshot.manifest["committed_doc_ids"], budget) as metadata:
            allowed = set(metadata.get_matching_docs(filters or {}))
            budget.check()
            eligible = [identifier for identifier in snapshot.ordered_ids
                        if snapshot.passages[identifier]["doc_id"] in allowed]
            matches = {}
            for atom in dict.fromkeys(token for token in parsed.postfix if isinstance(token, Atom)):
                budget.check()
                matches[atom] = atom_matches(atom, snapshot, eligible)
            candidates = evaluate(parsed.postfix, eligible, {atom: list(rows) for atom, rows in matches.items()})
            budget.check()
            details = metadata.get_metadata(list({snapshot.passages[identifier]["doc_id"] for identifier in candidates}))
            budget.check()

        # A missing metadata row cannot escape a request's eligibility check.
        candidates = [identifier for identifier in candidates if snapshot.passages[identifier]["doc_id"] in details]
        stats = snapshot.manifest["stats"]
        terms = sorted({term for atom in parsed.positive for term in atom.terms})
        idfs = {term: inverse_document_frequency(stats["N"], snapshot.frequency[term])
                for term in terms if term in snapshot.frequency}
        heap = TopK(page * k)
        for offset, identifier in enumerate(candidates):
            if offset % 128 == 0:
                budget.check()
            passage = snapshot.passages[identifier]
            item = details[passage["doc_id"]]
            score = sum(
                self.ranking.contribution(len(positions), passage["length"], stats["avgdl"], idf)
                for term, idf in idfs.items()
                if (positions := snapshot.postings[term].get(identifier))
            )
            date_rank = date.fromisoformat(item["issue_date"]).toordinal() if item.get("issue_date") else 0
            heap.push((score, date_rank, -identifier), (identifier, score))

        results = []
        for identifier, score in heap.descending()[(page - 1) * k:]:
            budget.check()
            passage = snapshot.passages[identifier]
            item = details[passage["doc_id"]]
            text, highlighted, spans = snippet(
                passage, {atom: matches[atom].get(identifier, []) for atom in parsed.positive},
            )
            results.append({
                "doc_id": passage["doc_id"], "scheme_id": item.get("scheme_id"),
                "passage_id": encode_passage_id(identifier), "title": item.get("title"),
                "snippet": text, "page_no": passage["page_no"], "department": item.get("department"),
                "issue_date": item.get("issue_date"), "score": float(score),
                "matched_terms": highlighted, "match_spans": spans, "source_type": item.get("source_type"),
                "superseded": item.get("superseded"), "current_doc_id": item.get("current_doc_id"),
            })
        budget.check()
        return ({
            "results": results, "did_you_mean": suggestion, "total": len(candidates),
            "page": page, "k": k, "generation": snapshot.manifest["generation"],
        }, parsed.warning)
