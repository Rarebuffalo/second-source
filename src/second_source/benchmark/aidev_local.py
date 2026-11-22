"""Read AIDev from a downloaded Parquet file instead of the dataset viewer API.

The Hugging Face dataset viewer is a free shared service and goes down for hours at a time
(HTTP 502/503), which kills an hour-long sampling run part way. The viewer serves the rows of
the dataset's Parquet conversion, so the same file, downloaded once, gives the same rows at the
same offsets, locally and in seconds:

    https://huggingface.co/datasets/hao-li/AIDev/resolve/refs%2Fconvert%2Fparquet/pull_request/train/0000.parquet

``ParquetAidev`` offers the two methods samplers use (``page`` and ``revision``), so it is a
drop-in replacement for ``AidevClient``. Rows go through ``AidevPullRequest.from_row``, so they
are mapped and redacted exactly as API rows are.

pyarrow is an optional dependency (``uv sync --extra local``); it is imported only when a
Parquet file is used, so the CLI and the GitHub Action never need it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from second_source.benchmark.aidev import MAX_PAGE, AidevError, AidevPullRequest, Page

# The columns AidevPullRequest.from_row reads; loading only these keeps memory low.
COLUMNS = ("html_url", "agent", "title", "body", "state", "created_at", "merged_at", "id")


class ParquetAidev:
    """The curated ``pull_request`` table, read from a local Parquet file."""

    def __init__(self, path: Path) -> None:
        try:
            import pyarrow.parquet as pq
        except ImportError as error:  # pragma: no cover - depends on the environment
            raise AidevError(
                "reading a Parquet file needs pyarrow: run `uv sync --extra local`"
            ) from error
        self._path = path
        try:
            self._table = pq.read_table(path, columns=list(COLUMNS), memory_map=True)
        except (OSError, ValueError) as error:
            raise AidevError(f"cannot read {path}: {error}") from error

    def __enter__(self) -> ParquetAidev:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Nothing to release; present so it can stand in for AidevClient."""

    def page(self, offset: int, length: int = MAX_PAGE) -> Page:
        """Rows ``offset .. offset+length-1``, like the dataset viewer's /rows endpoint."""
        if offset < 0 or not 0 < length <= MAX_PAGE:
            raise ValueError(f"invalid page: offset={offset}, length={length}")
        rows: list[dict[str, Any]] = self._table.slice(offset, length).to_pylist()
        return Page(
            rows=tuple(AidevPullRequest.from_row(_as_api_row(row)) for row in rows),
            total=len(self),
        )

    def revision(self) -> str:
        """The file's SHA-256, so a sample records exactly which data it was drawn from.

        A Parquet file carries no dataset revision; the file hash identifies it uniquely.
        """
        digest = hashlib.sha256()
        with self._path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return f"parquet-sha256:{digest.hexdigest()}"

    def __len__(self) -> int:
        return self._table.num_rows


def _as_api_row(row: dict[str, Any]) -> dict[str, Any]:
    """Parquet may type timestamps; the API returns ISO strings. Match the API."""
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in row.items()
    }
