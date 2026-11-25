"""On-disk ETag cache for the CLI and the benchmark.

Each cached response is one JSON file named after a hash of its URL, holding only the URL,
the ETag and the response body. Request headers (including any token) are never stored.
Unreadable or corrupt files are treated as cache misses: the cache can only save requests,
never break a run.

Nothing that holds code is stored: responses with diff patches (a PR's file list) are not
cached at all, and files and directories are readable by the owner only, because a cached PR
of a private repository is private data (security review 1, M5).
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
        if _holds_code(body):
            return
        path = self._path(url)
        try:
            for directory in (self.directory, path.parent):
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                directory.chmod(0o700)
            # Write to a temporary file and rename, so readers never see half-written files.
            temporary = path.with_suffix(".tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"url": url, "etag": etag, "body": body}))
            temporary.replace(path)
        except OSError:
            return  # a read-only or full disk must not fail the run

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.directory / digest[:2] / f"{digest}.json"


def _holds_code(body: Any) -> bool:
    """True for a response carrying diff patches (``GET /pulls/N/files``)."""
    return isinstance(body, list) and any(isinstance(i, dict) and "patch" in i for i in body)
