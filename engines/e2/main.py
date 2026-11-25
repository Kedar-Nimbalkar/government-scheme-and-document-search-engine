import os
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from search_shared import SCHEMA_VERSION
from search_shared.metadata import MetadataRepository

from .assistance import MAX_PREFIX
from .budget import Budget, RequestTimeout
from .models import AutocompleteResponse, ErrorDetail, ErrorResponse, HealthResponse, SearchRequest, SearchResponse
from .query import InvalidQuery
from .ranking import BM25
from .search import SearchEngine, SearchUnavailable, SnapshotCache

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
REQUEST_SLOTS = BoundedSemaphore(8)
ERRORS = {status: {"model": ErrorResponse} for status in (400, 503, 504)}


@app.get("/health", response_model=HealthResponse)
def health():
    return {"status": "ok", "service": "e2", "contract_version": SCHEMA_VERSION}


@contextmanager
def postgres_metadata(committed_ids, budget):
    url = os.environ.get("E2_DATABASE_URL")
    if not url:
        raise SearchUnavailable("Metadata database is unavailable")
    try:
        with psycopg.connect(url, connect_timeout=2) as connection:
            timeout = min(2000, max(1, int(budget.remaining() * 1000)))
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.execute("SELECT set_config('statement_timeout', %s, true)", (f"{timeout}ms",))
            yield MetadataRepository(connection, committed_ids)
    except psycopg.errors.QueryCanceled as error:
        raise RequestTimeout("Metadata query budget exhausted") from error
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
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, request_id=str(uuid4())))
    return JSONResponse(status_code=status, content=body.model_dump())


@contextmanager
def request_slot(seconds):
    if not REQUEST_SLOTS.acquire(blocking=False):
        raise SearchUnavailable("Query service is at capacity")
    try:
        yield Budget(seconds)
    finally:
        REQUEST_SLOTS.release()


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError):
    return error_response(400, "INVALID_REQUEST", "Invalid query service request")


@app.exception_handler(ResponseValidationError)
async def invalid_response(_request: Request, _error: ResponseValidationError):
    return error_response(503, "SERVICE_UNAVAILABLE", "Query result is unavailable")


@app.exception_handler(InvalidQuery)
async def bad_query(_request: Request, error: InvalidQuery):
    return error_response(400, "INVALID_REQUEST", str(error))


@app.exception_handler(SearchUnavailable)
async def unavailable(_request: Request, error: SearchUnavailable):
    return error_response(503, "SERVICE_UNAVAILABLE", str(error))


@app.exception_handler(RequestTimeout)
async def timeout_error(_request: Request, error: RequestTimeout):
    return error_response(504, "SEARCH_TIMEOUT", str(error))


@app.post("/internal/search", response_model=SearchResponse, responses=ERRORS)
def search(request: SearchRequest, response: Response):
    with request_slot(3) as budget:
        body, warning = get_engine().search(request.query, request.filters.model_dump(), request.k, request.page, budget)
    if warning:
        response.headers["X-Search-Warning"] = warning
    return body


@app.get("/internal/autocomplete", response_model=AutocompleteResponse, responses=ERRORS)
def autocomplete(prefix: Annotated[str, Query(min_length=1, max_length=MAX_PREFIX)]):
    with request_slot(1) as budget:
        return {"suggestions": get_engine().autocomplete(prefix, budget)}
