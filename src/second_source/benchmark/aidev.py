"""Read pull requests from the AIDev dataset without downloading it.

AIDev (``hao-li/AIDev`` on Hugging Face, CC-BY-4.0) is a public dataset of pull requests opened
by coding agents. Its curated ``pull_request`` table has about 361k rows. Instead of downloading
the Parquet files, we read 100-row pages through the Hugging Face dataset viewer API, which
needs no account and no disk. See docs/benchmark.md, section 1.

The dataset viewer always serves the latest dataset revision, so samplers record the revision
(``AidevClient.revision()``) next to their output; a later revision may shift row offsets.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from second_source import __version__

DATASET = "hao-li/AIDev"
CONFIG = "pull_request"  # the curated table (repositories with more than 100 stars)
ROWS_URL = "https://datasets-server.huggingface.co/rows"
INFO_URL = f"https://huggingface.co/api/datasets/{DATASET}"
MAX_PAGE = 100  # the dataset viewer returns at most 100 rows per request
USER_AGENT = f"second-source/{__version__}"

# AIDev's agent labels mapped to the names our detection rules use (packs/coding/detection.py).
AGENT_NAMES = {
    "Claude_Code": "claude_code",
    "OpenAI_Codex": "codex",
    "Copilot": "copilot",
    "Cursor": "cursor",
    "Devin": "devin",
    "Google_Jules": "jules",
}


class AidevError(Exception):
    """The dataset viewer API failed after retries, or answered with something unexpected."""


@dataclass(frozen=True)
class AidevPullRequest:
    """The fields of one AIDev row that the benchmark uses."""

    url: str
    agent: str  # our agent name (see AGENT_NAMES); the raw label if it is not mapped
    title: str
    body: str
    state: str
    created_at: str | None
    merged_at: str | None

    @property
    def merged(self) -> bool:
        return bool(self.merged_at)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> AidevPullRequest:
        label = row.get("agent") or ""
        return cls(
            url=row["html_url"],
            agent=AGENT_NAMES.get(label, label),
            title=row.get("title") or "",
            body=row.get("body") or "",
            state=row.get("state") or "",
            created_at=row.get("created_at"),
            merged_at=row.get("merged_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "agent": self.agent,
            "title": self.title,
            "body": self.body,
            "state": self.state,
            "created_at": self.created_at,
            "merged_at": self.merged_at,
        }


@dataclass(frozen=True)
class Page:
    """One page of rows plus the table's total row count."""

    rows: tuple[AidevPullRequest, ...]
    total: int


class AidevClient:
    """Thin client for the dataset viewer ``/rows`` endpoint.

    The transport and sleep function are injectable so tests never touch the network or wait.
    """

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        self._http = httpx.Client(
            transport=transport, headers={"User-Agent": USER_AGENT}, timeout=timeout
        )
        self._retries = retries
        self._sleep = sleep

    def __enter__(self) -> AidevClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def page(self, offset: int, length: int = MAX_PAGE) -> Page:
        """Rows ``offset .. offset+length-1`` of the curated pull request table."""
        if offset < 0 or not 0 < length <= MAX_PAGE:
            raise ValueError(f"invalid page: offset={offset}, length={length}")
        params = {
            "dataset": DATASET,
            "config": CONFIG,
            "split": "train",
            "offset": offset,
            "length": length,
        }
        data = self._get_json(ROWS_URL, params)
        try:
            rows = tuple(AidevPullRequest.from_row(item["row"]) for item in data["rows"])
            return Page(rows=rows, total=int(data["num_rows_total"]))
        except (KeyError, TypeError, ValueError) as error:
            raise AidevError(f"unexpected response shape: {error!r}") from error

    def revision(self) -> str:
        """The dataset's current git revision (commit sha), recorded next to samples."""
        data = self._get_json(INFO_URL, {})
        try:
            return str(data["sha"])
        except (KeyError, TypeError) as error:
            raise AidevError("dataset info has no revision") from error

    def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        # Retry server errors, rate limits and network failures with exponential backoff.
        # The dataset viewer answers with an "index is loading" error while it warms up,
        # which is also worth waiting for.
        last_problem = ""
        for attempt in range(self._retries + 1):
            if attempt:
                self._sleep(2.0**attempt)
            try:
                response = self._http.get(url, params=params)
            except httpx.TransportError as error:
                last_problem = f"network error: {error.__class__.__name__}"
                continue
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict) and "error" in data:
                    last_problem = str(data["error"])
                    continue
                return data
            last_problem = f"HTTP {response.status_code}"
            if response.status_code < 500 and response.status_code != 429:
                break  # a client error will not fix itself
        raise AidevError(f"dataset viewer request failed: {last_problem}")
