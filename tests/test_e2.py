import asyncio
import json
import math
import os
import random
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from search_shared.metadata import normalize_filter
from search_shared.tokenizer import tokenize_with_spans

from engines.e1.parallel import run_parallel
from engines.e1.reference import compile_segment, load_pdf_jobs
from engines.e1.snapshot import SnapshotWriter, index_sources
from engines.e2 import main as api
from engines.e2.query import Atom, InvalidQuery, parse
from engines.e2.ranking import TopK, snippet
from engines.e2.search import SearchEngine, SnapshotCache

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "fixtures/e2-labelled/corpus.json").read_text(encoding="utf-8"))


@contextmanager
def http_search():
    with asyncio.Runner() as runner:
        client = AsyncClient(transport=ASGITransport(app=api.app), base_url="http://e2.test")
        try:
            yield lambda **kwargs: runner.run(client.post("/internal/search", **kwargs))
        finally:
            runner.run(client.aclose())


class FixtureMetadata:
    def __init__(self, documents, committed):
        self.documents = {
            item["doc_id"]: item for item in documents if item["indexed"] and item["doc_id"] in committed
        }

    def get_matching_docs(self, filters):
        active = {name: value for name, raw in filters.items() if (value := normalize_filter(raw))}
        return {identifier for identifier, item in self.documents.items()
                if all(normalize_filter(item.get(name)) == value for name, value in active.items())}

    def get_metadata(self, doc_ids):
        return {identifier: self.documents[identifier] for identifier in doc_ids}


@pytest.fixture
def labelled(tmp_path):
    documents = FIXTURE["documents"]
    parsed = [
        (doc["doc_id"], [(item["page_no"], item["text"], tokenize_with_spans(item["text"])) for item in doc["passages"]])
        for doc in documents
    ]
    segment, next_id = compile_segment(parsed, 1)
    SnapshotWriter(tmp_path).append(segment, {doc["doc_id"] for doc in documents}, next_id)

    @contextmanager
    def metadata(committed):
        yield FixtureMetadata(documents, committed)

    return SearchEngine(SnapshotCache(tmp_path), metadata)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[case["query"] for case in FIXTURE["cases"]])
def test_labelled_search(labelled, case):
    response, warning = labelled.search(case["query"], case.get("filters"), k=20)
    assert {hit["title"].removeprefix("Synthetic ") for hit in response["results"]} == set(case["labels"])
    assert response["total"] == len(case["labels"])
    assert warning == case.get("warning")


def test_default_or_filter_reset_negative_scoring_and_duplicate_terms(labelled):
    implicit, _ = labelled.search("housing water", k=20)
    explicit, _ = labelled.search("housing OR water", k=20)
    assert implicit == explicit
    selected, _ = labelled.search("housing water", {"state": "goa"}, k=20)
    assert {hit["title"] for hit in selected["results"]} == {"Synthetic coastal", "Synthetic water", "Synthetic water-repeat"}
    reset, _ = labelled.search("housing water", {"state": "  "}, k=20)
    assert reset == implicit
    repeated, _ = labelled.search("housing OR housing", k=20)
    single, _ = labelled.search("housing", k=20)
    inverted_twice, _ = labelled.search("NOT NOT housing", k=20)
    assert repeated == single == inverted_twice
    [repeat] = labelled.search('"grant grant"')[0]["results"]
    assert repeat["title"] == "Synthetic rural" and len(repeat["match_spans"]) == 2
    # The labelled snapshot has 16 passages, 79 tokens and grant df=12.
    known_idf = math.log(1 + 4.5 / 12.5)
    expected = known_idf * (2 * 2.2) / (2 + 1.2 * (0.25 + 0.75 * 6 / (79 / 16)))
    assert repeat["score"] == pytest.approx(expected)
    negative, _ = labelled.search("NOT grant", {"state": "Karnataka"})
    [csv] = negative["results"]
    assert (csv["score"], csv["matched_terms"], csv["match_spans"], csv["page_no"]) == (0, [], [], None)
    assert csv["source_type"] == "CSV" and csv["issue_date"] is None


def test_ties_pagination_unicode_and_no_highlight_for_failed_phrase(labelled):
    full, _ = labelled.search('"housing grant"', k=20)
    titles = [hit["title"] for hit in full["results"]]
    assert titles.index("Synthetic tie-a") < titles.index("Synthetic tie-b") < titles.index("Synthetic coastal")
    assert titles.index("Synthetic coastal") < titles.index("Synthetic tie-unknown")
    for page in range(1, 6):
        response, _ = labelled.search('"housing grant"', k=2, page=page)
        assert response["results"] == full["results"][(page - 1) * 2:page * 2]
        assert response["total"] == full["total"]
    featured = next(hit for hit in full["results"] if hit["title"] == "Synthetic utf16")
    assert featured["snippet"].startswith("🏠 Housing")
    assert featured["match_spans"][0] == {"start": 3, "end": 10}
    response, _ = labelled.search('"housing grant" OR support', k=20)
    separated = next(hit for hit in response["results"] if hit["title"] == "Synthetic separated")
    assert separated["matched_terms"] == ["support"]


def test_short_snippet_rebases_utf16_offsets():
    text = "🏠 prefix " * 55 + "housing grant " + "suffix " * 50
    positioned = tokenize_with_spans(text)
    pos = next(i for i, (term, _) in enumerate(positioned) if term == "housing")
    copy, terms, spans = snippet({"text": text, "token_spans": [span for _, span in positioned]},
                                 {Atom(("housing", "grant")): [pos]})
    assert copy in text and copy != text
    encoded = copy.encode("utf-16-le")
    assert len(encoded) <= 560
    assert [encoded[2 * s["start"]:2 * s["end"]].decode("utf-16-le") for s in spans] == ["housing", "grant"]
    assert terms == ["grant", "housing"]


def test_heap_matches_sort():
    rng = random.Random(6)
    keys = [(rng.randint(0, 8), rng.randint(0, 4), -i) for i in range(100)]
    for capacity in (1, 2, 10, 37, 120):
        heap = TopK(capacity)
        for key in keys:
            heap.push(key, key)
        assert heap.descending() == sorted(keys, reverse=True)[:capacity]


def test_known_score_shared_fixture_and_phrase_repetition():
    root = ROOT / "fixtures/v2/index"
    snapshot = SnapshotCache(root)
    manifest = snapshot.get().manifest
    [doc_id] = manifest["committed_doc_ids"]

    @contextmanager
    def metadata(_):
        yield FixtureMetadata([{"indexed": True, "doc_id": doc_id}], [doc_id])

    engine = SearchEngine(snapshot, metadata)
    response, _ = engine.search("housing")
    assert response["results"][0]["score"] == pytest.approx(math.log(4 / 3))
    assert engine.search('"housing housing"')[0]["total"] == 0
    with pytest.raises(InvalidQuery):
        parse("a " * 65)


def test_http_fixture_contract_validation_and_warning(labelled, monkeypatch):
    monkeypatch.setattr(api, "get_engine", lambda: labelled)
    with http_search() as post:
        response = post(json={"query": 'housing AND (water'})
        assert response.status_code == 200
        assert response.headers["X-Search-Warning"] == "BOOLEAN_FALLBACK"
        assert set(response.json()) == {"results", "did_you_mean", "total", "page", "k", "generation"}
        assert set(response.json()["results"][0]) == {
            "doc_id", "scheme_id", "passage_id", "title", "snippet", "page_no", "department", "issue_date",
            "score", "matched_terms", "match_spans", "source_type", "superseded", "current_doc_id",
        }
        for payload in [
            {"query": 42}, {"query": " "}, {"query": "a" * 513}, {"query": "a " * 65},
            {"query": "housing", "k": 51}, {"query": "housing", "page": True},
            {"query": "housing", "page": 21, "k": 50}, {"query": "housing", "filters": []},
            {"query": "housing", "filters": {"state": 12}}, {"query": "housing", "filters": {"injected": "x"}},
        ]:
            rejected = post(json=payload)
            assert rejected.status_code == 400
            assert rejected.json()["error"]["code"] == "INVALID_REQUEST"
        assert post(content="{").status_code == 400


def test_real_reader_reload_and_db_unavailable(tmp_path, monkeypatch):
    fixture = ROOT / "fixtures/e1-synthetic"
    [source] = load_pdf_jobs(fixture / "pdf-jobs.json", fixture)
    index_sources(tmp_path, [], max_tokens=256)
    cache = SnapshotCache(tmp_path)
    assert cache.get().manifest["generation"] == 0
    index_sources(tmp_path, [source])
    assert cache.get().manifest["generation"] == 1
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path))
    monkeypatch.delenv("E2_DATABASE_URL", raising=False)
    api.get_engine.cache_clear()
    try:
        with http_search() as post:
            response = post(json={"query": "grant", "filters": {"state": "Goa"}})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    finally:
        api.get_engine.cache_clear()


def test_real_postgres_and_spawned_e1_search(tmp_path, monkeypatch):
    database = os.environ.get("E2_TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set E2_TEST_DATABASE_URL for standalone E2 integration")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    schema = "e2_test_" + uuid4().hex
    fixture = ROOT / "fixtures/e1-synthetic"
    [source] = load_pdf_jobs(fixture / "pdf-jobs.json", fixture)
    options = f"-c search_path={schema},public"
    with psycopg.connect(database, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with psycopg.connect(make_conninfo(database, options=options), autocommit=True) as db:
                for migration in sorted((ROOT / "db/migrations").glob("*.sql")):
                    db.execute(migration.read_text(encoding="utf-8"), prepare=False)
                db.execute((ROOT / "db/runtime-roles.sql").read_text(encoding="utf-8"), prepare=False)
                dept = db.execute("INSERT INTO department(dept_name,level) VALUES ('Housing Department','State') RETURNING dept_id").fetchone()[0]
                db.execute("""INSERT INTO scheme(scheme_id,dept_id,scheme_name,state,category)
                           VALUES (%s,%s,'Synthetic Housing','Karnataka','Housing')""", (source.scheme_id, dept))
                db.execute("""INSERT INTO document(doc_id,scheme_id,title,source_type,file_path,content_hash)
                           VALUES (%s,%s,'Synthetic document','PDF',%s,%s)""",
                           (source.doc_id, source.scheme_id, source.pdf_path.name,
                            "7db710416abcee81e5b737b6155cb4bac74325c012e9836a84e13768fae55369"))
                db.execute("INSERT INTO ingestion_job(job_id,doc_id) VALUES (%s,%s)", (source.job_id, source.doc_id))
                absent = db.execute("""
                    INSERT INTO document(scheme_id,title,source_type) VALUES (%s,'DB only','CSV') RETURNING doc_id
                """, (source.scheme_id,)).fetchone()[0]
                db.execute("INSERT INTO ingestion_job(doc_id,status) VALUES (%s,'INDEXED')", (absent,))
            worker = make_conninfo(database, options=f"{options} -c role=team06_e1")
            report = run_parallel(tmp_path, workers=2, database_url=worker, pdf_root=fixture)
            assert [entry["status"] for entry in report["documents"]] == ["INDEXED"]
            reader = make_conninfo(database, options=f"{options} -c role=team06_e2")
            monkeypatch.setenv("INDEX_ROOT", str(tmp_path))
            monkeypatch.setenv("E2_DATABASE_URL", reader)
            api.get_engine.cache_clear()
            with http_search() as post:
                payload = {"query": '"housing grant"', "filters": {"state": " ＫＡＲＮＡＴＡＫＡ ", "department": "housing department"}}
                response = post(json=payload)
                assert response.status_code == 200
                data = response.json()
                assert data["total"] == 1
                assert {result["doc_id"] for result in data["results"]} == {source.doc_id}
                assert {result["page_no"] for result in data["results"]} == {3}
                assert [hit["page_no"] for hit in post(json={"query": '"grant housing"'}).json()["results"]] == [1]
                payload["filters"]["state"] = "Goa"
                assert post(json=payload).json()["total"] == 0
                assert post(json={"query": "NOT absent"}).json()["total"] == 2
                samples = []
                for _ in range(20):
                    started = time.perf_counter()
                    assert post(json={"query": '"housing grant"', "filters": {"state": "karnataka"}}).status_code == 200
                    samples.append((time.perf_counter() - started) * 1000)
                ordered = sorted(samples)
                print(f"E2 real E1/E3 warm requests: n=20 p50={sum(ordered[9:11])/2:.2f}ms p95={ordered[18]:.2f}ms")
                monkeypatch.setenv("E2_DATABASE_URL", worker)
                unavailable = post(json={"query": "housing", "filters": {"state": "Goa"}})
                assert unavailable.status_code == 503
        finally:
            api.get_engine.cache_clear()
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
