import os
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from search_shared import SCHEMA_VERSION
from search_shared.metadata import MetadataRepository

from .query import MAX_K, MAX_QUERY_LENGTH, InvalidQuery
from .ranking import BM25
from .search import SearchEngine, SearchUnavailable, SnapshotCache

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health() -> dict[str, str | int]:
    return {"status": "ok", "service": "e2", "contract_version": SCHEMA_VERSION}


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: str | None = None
    department: str | None = None
    category: str | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    filters: Filters = Field(default_factory=Filters)
    k: int = Field(default=10, ge=1, le=MAX_K)
    page: int = Field(default=1, ge=1)


@contextmanager
def postgres_metadata(committed_ids):
    url = os.environ.get("E2_DATABASE_URL")
    if not url:
        raise SearchUnavailable("Metadata database is unavailable")
    try:
        with psycopg.connect(url, connect_timeout=3) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '2s'")
            yield MetadataRepository(connection, committed_ids)
    except psycopg.Error as error:
        raise SearchUnavailable("Metadata database is unavailable") from error


@lru_cache(maxsize=1)
def get_engine():
    try:
        ranking = BM25(float(os.environ.get("E2_BM25_K1", "1.2")), float(os.environ.get("E2_BM25_B", "0.75")))
    except ValueError as error:
        raise SearchUnavailable("Search configuration is unavailable") from error
    return SearchEngine(SnapshotCache(Path(os.environ.get("INDEX_ROOT", "data/index"))), postgres_metadata, ranking)


def error_response(status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={
        "error": {"code": code, "message": message, "request_id": str(uuid4())},
    })


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError):
    return error_response(400, "INVALID_REQUEST", "Invalid search request")


@app.post("/internal/search")
def search(request: SearchRequest, response: Response):
    try:
        body, warning = get_engine().search(request.query, request.filters.model_dump(), request.k, request.page)
    except InvalidQuery as error:
        return error_response(400, "INVALID_REQUEST", str(error))
    except SearchUnavailable as error:
        return error_response(503, "SERVICE_UNAVAILABLE", str(error))
    if warning:
        response.headers["X-Search-Warning"] = warning
    return body
