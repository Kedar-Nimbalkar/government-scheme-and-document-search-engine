from fastapi import FastAPI
from search_shared import SCHEMA_VERSION

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health() -> dict[str, str | int]:
    return {"status": "ok", "service": "e2", "contract_version": SCHEMA_VERSION}
