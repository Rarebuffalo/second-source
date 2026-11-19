"""A small, careful GitHub REST client.

Responsibilities:

- send authenticated requests (token optional) with a fixed, anonymous user agent;
- follow pagination (``Link: <...>; rel="next"``) up to a caller-chosen page limit;
- reuse cached responses with ETags (``If-None-Match`` → ``304 Not Modified``);
- detect rate limiting and raise ``RateLimited`` instead of retrying forever;
- retry transient failures (5xx, network errors) a couple of times with backoff.

Everything that varies (HTTP transport, cache, sleep) is injectable so tests never touch the
network or wait on real time.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from second_source import __version__

API_ROOT = "https://api.github.com"
USER_AGENT = f"second-source/{__version__}"
_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubError(Exception):
    """An unexpected response from GitHub (after retries)."""

    def __init__(self, status: int, url: str, message: str = "") -> None:
        super().__init__(f"GitHub returned {status} for {url} {message}".strip())
        self.status = status
        self.url = url


class NotFound(GitHubError):
    """404: the resource does not exist or the token cannot see it."""


class RateLimited(GitHubError):
    """The API rate limit is exhausted. Callers should stop and mark data stale."""


class PageLimitReached(GitHubError):
    """More pages exist than the caller allowed. Callers should treat the data as partial."""


class ResponseCache(Protocol):
    """Stores the last good response per URL, with its ETag."""

    def get(self, url: str) -> tuple[str, Any] | None: ...

    def put(self, url: str, etag: str, body: Any) -> None: ...


@dataclass
class MemoryCache:
    """In-process cache (used by the GitHub Action and in tests)."""

    entries: dict[str, tuple[str, Any]] = field(default_factory=dict)

    def get(self, url: str) -> tuple[str, Any] | None:
        return self.entries.get(url)

    def put(self, url: str, etag: str, body: Any) -> None:
        self.entries[url] = (etag, body)


@dataclass
class RequestStats:
    """Counts what a run cost, so the CLI can show it and tests can assert on it."""

    requests: int = 0
    not_modified: int = 0


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        cache: ResponseCache | None = None,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 20.0,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=API_ROOT, headers=headers, transport=transport, timeout=timeout
        )
        self._cache = cache or MemoryCache()
        self._retries = retries
        self._sleep = sleep
        self.stats = RequestStats()
        self.authenticated = bool(token)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- public API --------------------------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET one resource and return its JSON body."""
        body, _next = self._get_page(self._url(path, params))
        return body

    def get_paginated(
        self, path: str, params: dict[str, Any] | None = None, *, max_pages: int = 3
    ) -> list[Any]:
        """GET a list resource across pages (100 items per page).

        Raises PageLimitReached if more pages remain after ``max_pages``, so callers never
        mistake a truncated list for a complete one.
        """
        url: str | None = self._url(path, {"per_page": 100, **(params or {})})
        items: list[Any] = []
        for _ in range(max_pages):
            assert url is not None
            body, url = self._get_page(url)
            # Some list endpoints wrap items (e.g. check-runs: {"check_runs": [...]}).
            items.extend(body if isinstance(body, list) else _unwrap(body))
            if url is None:
                return items
        raise PageLimitReached(200, path, f"(more than {max_pages} pages)")

    # ---- internals ---------------------------------------------------------------------------

    def _url(self, path: str, params: dict[str, Any] | None) -> str:
        request = self._http.build_request("GET", path, params=params)
        return str(request.url)

    def _get_page(self, url: str) -> tuple[Any, str | None]:
        """Fetch one page, using the cache and retrying transient failures."""
        cached = self._cache.get(url)
        headers = {"If-None-Match": cached[0]} if cached else {}

        for attempt in range(self._retries + 1):
            try:
                response = self._http.get(url, headers=headers)
            except httpx.TransportError as error:
                if attempt == self._retries:
                    raise GitHubError(0, url, f"network error: {error}") from error
                self._sleep(2**attempt)
                continue
            self.stats.requests += 1

            if response.status_code == 304 and cached:
                self.stats.not_modified += 1
                return cached[1], _next_link(response)
            if response.status_code == 200:
                body = response.json()
                etag = response.headers.get("ETag")
                if etag:
                    self._cache.put(url, etag, body)
                return body, _next_link(response)
            if response.status_code == 404:
                raise NotFound(404, url)
            if _is_rate_limited(response):
                raise RateLimited(response.status_code, url, "(rate limit exhausted)")
            if response.status_code >= 500 and attempt < self._retries:
                self._sleep(2**attempt)
                continue
            raise GitHubError(response.status_code, url)
        raise AssertionError("unreachable")  # the loop always returns or raises


def _next_link(response: httpx.Response) -> str | None:
    found = _NEXT_LINK.search(response.headers.get("Link", ""))
    return found.group(1) if found else None


def _is_rate_limited(response: httpx.Response) -> bool:
    """GitHub signals rate limits with 403 or 429 plus specific headers."""
    if response.status_code not in (403, 429):
        return False
    return (
        response.headers.get("x-ratelimit-remaining") == "0"
        or "retry-after" in response.headers
        or response.status_code == 429
    )


def _unwrap(body: dict[str, Any]) -> list[Any]:
    """Return the single list inside a wrapped list response (e.g. ``check_runs``)."""
    lists = [value for value in body.values() if isinstance(value, list)]
    if len(lists) != 1:
        raise GitHubError(200, "", "unexpected list response shape")
    return lists[0]
