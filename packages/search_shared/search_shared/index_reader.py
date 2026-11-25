import json
import re
from pathlib import Path


def read_snapshot(index_root: str | Path) -> tuple[dict, list[dict]]:
    """Read exactly the immutable segments named by one manifest snapshot."""
    root = Path(index_root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["version"] != 1:
        raise ValueError("Unsupported index manifest version")
    segments = []
    for filename in manifest["segments"]:
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.json", filename):
            raise ValueError("Invalid segment filename")
        segment = json.loads((root / filename).read_text(encoding="utf-8"))
        if segment["version"] != 1:
            raise ValueError("Unsupported index segment version")
        segments.append(segment)
    return manifest, segments
