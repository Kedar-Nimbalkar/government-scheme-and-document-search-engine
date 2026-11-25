"""Manifest publication for the sequential reference writer."""

import hashlib
import heapq
import json
import os
import time
from contextlib import contextmanager
from itertools import groupby, repeat
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
            for attempt in range(21):
                try:
                    os.replace(staging_path, path)
                    break
                except PermissionError:
                    if os.name != "nt" or attempt == 20:
                        raise
                    time.sleep(0.01)
        finally:
            staging_path.unlink(missing_ok=True)
    if os.name != "nt":
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


class SnapshotWriter:
    """Mutate one manifest under the caller's exclusive writer lock."""

    def __init__(self, root: Path, publish_hook=None):
        self.root = root
        self.path = root / "manifest.json"
        self.publish_hook = publish_hook
        if self.path.exists():
            self.manifest, _ = read_snapshot(root)
        else:
            self.manifest = {
                "schema_version": SCHEMA_VERSION, "tokenizer_version": TOKENIZER_VERSION,
                "generation": 0, "segments": [], "next_passage_id": 1, "committed_doc_ids": [],
                "stats": {"N": 0, "total_tokens": 0, "avgdl": 0},
            }
        self.committed = set(self.manifest["committed_doc_ids"])
        self.next_id = passage_id_value(self.manifest["next_passage_id"])
        for abandoned in root.glob(".e1-*"):
            if abandoned.is_file():
                abandoned.unlink()

    def _segment(self, segment: dict) -> dict:
        contents = _json_bytes(segment)
        digest = hashlib.sha256(contents).hexdigest()
        filename = f"segment-{digest}.json"
        destination = self.root / filename
        if not destination.exists():
            _publish(destination, contents)
        elif destination.read_bytes() != contents:
            raise ValueError("Existing segment does not match its content hash")
        return {"file": filename, "sha256": digest}

    def _commit(self, manifest: dict) -> None:
        if self.path.exists():
            _publish(self.root / "manifest.previous.json", self.path.read_bytes())
        if self.publish_hook is not None:
            self.publish_hook("before_manifest")
        _publish(self.path, _json_bytes(manifest))
        if self.publish_hook is not None:
            self.publish_hook("after_manifest")
        self.manifest = manifest

    def append(self, segment: dict, indexed: set[str], next_id: int) -> None:
        if not indexed:
            if not self.path.exists():
                _publish(self.path, _json_bytes(self.manifest))
            return
        entry = self._segment(segment)
        previous = self.manifest
        count = previous["stats"]["N"] + len(segment["passages"])
        tokens = previous["stats"]["total_tokens"] + sum(p["length"] for p in segment["passages"])
        self._commit({
            **previous, "segments": [*previous["segments"], entry],
            "generation": previous["generation"] + 1, "next_passage_id": encode_passage_id(next_id),
            "committed_doc_ids": sorted(self.committed | indexed),
            "stats": {"N": count, "total_tokens": tokens, "avgdl": tokens / count},
        })
        self.committed.update(indexed)
        self.next_id = next_id

    def compact(self) -> None:
        if len(self.manifest["segments"]) < 2:
            return
        _, segments = read_snapshot(self.root)
        compacted = merge_segments(segments)
        self._commit({
            **self.manifest, "segments": [self._segment(compacted)],
            "generation": self.manifest["generation"] + 1,
        })


def merge_segments(segments: list[dict]) -> dict:
    """Heap-merge sorted dictionaries, postings and passages, keeping existing IDs."""
    dictionaries = [zip(segment["dictionary"], repeat(i))
                    for i, segment in enumerate(segments)]
    postings = {}

    for term, entries in groupby(heapq.merge(*dictionaries), key=lambda item: item[0]):
        postings[term] = list(heapq.merge(
            *(segments[index]["postings"][term] for _, index in entries),
            key=lambda item: passage_id_value(item["passage_id"]),
        ))
    return {
        "schema_version": SCHEMA_VERSION, "tokenizer_version": TOKENIZER_VERSION,
        "dictionary": list(postings), "postings": postings,
        "df": {term: len(rows) for term, rows in postings.items()},
        "passages": list(heapq.merge(
            *(segment["passages"] for segment in segments),
            key=lambda item: passage_id_value(item["passage_id"]),
        )),
    }


def index_sources(root: Path, sources: list[Source], max_tokens: int = 256) -> dict:
    """Append a verified batch with the original sequential parser and writer."""
    root.mkdir(parents=True, exist_ok=True)
    with _writer_lock(root):
        writer = SnapshotWriter(root)
        segment, indexed, next_id, reports = build_segment(sources, writer.committed, writer.next_id, max_tokens)
        writer.append(segment, indexed, next_id)
        return {"generation": writer.manifest["generation"], "stats": writer.manifest["stats"], "documents": reports}
