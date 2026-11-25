import hashlib
import json
import re
from pathlib import Path

from . import SCHEMA_VERSION
from .tokenizer import TOKENIZER_VERSION

SAFE_INTEGER_MAX = 2**53 - 1


def _check_version(payload: dict) -> None:
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported index schema version")
    if payload.get("tokenizer_version") != TOKENIZER_VERSION:
        raise ValueError("Unsupported tokenizer version")


def passage_id_value(value: int | str) -> int:
    """Decode a passage ID without losing precision or accepting alternate encodings."""
    if type(value) is int and 1 <= value <= SAFE_INTEGER_MAX:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        number = int(value)
        if number > SAFE_INTEGER_MAX:
            return number
    raise ValueError("Invalid passage ID encoding")


def encode_passage_id(value: int) -> int | str:
    """Encode a writer-allocated ID as its canonical JSON representation."""
    if type(value) is not int or value < 1:
        raise ValueError("Invalid passage ID")
    return value if value <= SAFE_INTEGER_MAX else str(value)


def read_snapshot(index_root: str | Path) -> tuple[dict, list[dict]]:
    """Read exactly the immutable segments named by one manifest snapshot."""
    root = Path(index_root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    _check_version(manifest)
    next_id = passage_id_value(manifest["next_passage_id"])
    ids = set()
    docs = set()
    filenames = set()
    segments = []
    for entry in manifest["segments"]:
        filename = entry["file"]
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.json", filename):
            raise ValueError("Invalid segment filename")
        if filename in filenames:
            raise ValueError("Duplicate segment filename")
        filenames.add(filename)
        content = (root / filename).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError("Index segment checksum mismatch")
        segment = json.loads(content.decode("utf-8"))
        _check_version(segment)
        segment_docs = set()
        for passage in segment["passages"]:
            identifier = passage_id_value(passage["passage_id"])
            if identifier in ids or identifier >= next_id:
                raise ValueError("Invalid or repeated passage ID in snapshot")
            ids.add(identifier)
            segment_docs.add(passage["doc_id"])
        if docs & segment_docs:
            raise ValueError("Document indexed in multiple segments")
        docs.update(segment_docs)
        segments.append(segment)
    committed = manifest["committed_doc_ids"]
    if len(set(committed)) != len(committed) or not docs.issubset(committed):
        raise ValueError("Invalid committed document inventory")
    return manifest, segments
