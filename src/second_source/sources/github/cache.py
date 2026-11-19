"""On-disk ETag cache for the CLI and the benchmark.

Each cached response is one JSON file named after a hash of its URL, holding only the URL,
the ETag and the response body. Request headers (including any token) are never stored.
Unreadable or corrupt files are treated as cache misses: the cache can only save requests,
never break a run.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def default_cache_dir() -> Path:
    """``$XDG_CACHE_HOME/second-source/github``, or ``~/.cache/second-source/github``."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "second-source" / "github"


class DiskCache:
    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory else default_cache_dir()

    def get(self, url: str) -> tuple[str, Any] | None:
        path = self._path(url)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        # Guard against hash collisions or hand-edited files.
        if entry.get("url") != url or "etag" not in entry:
            return None
        return entry["etag"], entry.get("body")

    def put(self, url: str, etag: str, body: Any) -> None:
        path = self._path(url)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temporary file and rename, so readers never see half-written files.
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"url": url, "etag": etag, "body": body}), "utf-8")
            temporary.replace(path)
        except OSError:
            return  # a read-only or full disk must not fail the run

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.directory / digest[:2] / f"{digest}.json"
