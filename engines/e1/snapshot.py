"""Manifest publication for the sequential reference writer."""

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from search_shared import SCHEMA_VERSION
from search_shared.index_reader import encode_passage_id, passage_id_value, read_snapshot
from search_shared.tokenizer import TOKENIZER_VERSION

from .reference import Source, build_segment


@contextmanager
def _writer_lock(root: Path):
    # Keep the lock file on disk so contenders always lock the same file.
    with (root / ".writer.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _publish(path: Path, content: bytes) -> None:
    with NamedTemporaryFile(dir=path.parent, prefix=".e1-", delete=False) as staged:
        staging_path = Path(staged.name)
        try:
            staged.write(content)
            staged.flush()
            os.fsync(staged.fileno())
            staged.close()
            os.replace(staging_path, path)
        finally:
            staging_path.unlink(missing_ok=True)
    if os.name != "nt":
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def index_sources(root: Path, sources: list[Source], max_tokens: int = 256) -> dict:
    """Append a verified batch and return its extraction report."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manifest.json"
    with _writer_lock(root):
        if path.exists():
            manifest, _ = read_snapshot(root)
        else:
            manifest = {
                "schema_version": SCHEMA_VERSION, "tokenizer_version": TOKENIZER_VERSION,
                "generation": 0, "segments": [], "next_passage_id": 1, "committed_doc_ids": [],
                "stats": {"N": 0, "total_tokens": 0, "avgdl": 0},
            }
        committed = set(manifest["committed_doc_ids"])
        segment, indexed, next_id, reports = build_segment(
            sources, committed, passage_id_value(manifest["next_passage_id"]), max_tokens,
        )
        if indexed:
            contents = _json_bytes(segment)
            digest = hashlib.sha256(contents).hexdigest()
            filename = f"segment-{digest}.json"
            destination = root / filename
            if not destination.exists():
                _publish(destination, contents)
            elif destination.read_bytes() != contents:
                raise ValueError("Existing segment does not match its content hash")
            manifest["segments"].append({"file": filename, "sha256": digest})
            manifest["generation"] += 1
            manifest["next_passage_id"] = encode_passage_id(next_id)
            manifest["committed_doc_ids"] = sorted(committed | indexed)
            stats = manifest["stats"]
            stats["N"] += len(segment["passages"])
            stats["total_tokens"] += sum(passage["length"] for passage in segment["passages"])
            stats["avgdl"] = stats["total_tokens"] / stats["N"]
        if indexed or not path.exists():
            _publish(path, _json_bytes(manifest))
        return {"generation": manifest["generation"], "stats": manifest["stats"], "documents": reports}
