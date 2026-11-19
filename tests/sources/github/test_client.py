"""Tests for the GitHub REST client (sources/github/client.py). No network: MockTransport."""

import httpx
import pytest

from second_source.sources.github.client import (
    USER_AGENT,
    GitHubClient,
    GitHubError,
    MemoryCache,
    NotFound,
    PageLimitReached,
    RateLimited,
)


def make_client(handler, **kwargs):
    """A client whose HTTP calls go to `handler` and never really sleep."""
    return GitHubClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None, **kwargs)


def test_sends_api_headers_and_token():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    make_client(handler, token="secret-token").get("/repos/o/r")
    assert seen["authorization"] == "Bearer secret-token"
    assert seen["user-agent"] == USER_AGENT
    assert seen["x-github-api-version"] == "2022-11-28"


def test_no_authorization_header_without_token():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={})

    client = make_client(handler)
    client.get("/repos/o/r")
    assert "authorization" not in seen
    assert client.authenticated is False


def test_follows_pagination_and_unwraps_wrapped_lists():
    pages = {
        "1": httpx.Response(
            200,
            json={"total_count": 3, "check_runs": [{"id": 1}, {"id": 2}]},
            headers={"Link": '<https://api.github.com/x?per_page=100&page=2>; rel="next"'},
        ),
        "2": httpx.Response(200, json={"total_count": 3, "check_runs": [{"id": 3}]}),
    }

    def handler(request):
        return pages[request.url.params.get("page", "1")]

    items = make_client(handler).get_paginated("/x")
    assert [item["id"] for item in items] == [1, 2, 3]


def test_page_limit_is_reported_instead_of_silently_truncating():
    def handler(request):
        return httpx.Response(
            200,
            json=[{"id": 1}],
            headers={"Link": '<https://api.github.com/x?page=9>; rel="next"'},
        )

    with pytest.raises(PageLimitReached):
        make_client(handler).get_paginated("/x", max_pages=2)


def test_etag_cache_serves_not_modified_responses():
    calls = []

    def handler(request):
        calls.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json={"n": 1}, headers={"ETag": '"v1"'})

    cache = MemoryCache()
    client = make_client(handler, cache=cache)
    assert client.get("/r") == {"n": 1}
    assert client.get("/r") == {"n": 1}  # served from cache after a 304
    assert calls == [None, '"v1"']
    assert client.stats.not_modified == 1


def test_cache_never_stores_the_token():
    cache = MemoryCache()

    def handler(request):
        return httpx.Response(200, json={"n": 1}, headers={"ETag": '"v1"'})

    make_client(handler, token="secret-token", cache=cache).get("/r")
    assert "secret-token" not in repr(cache.entries)


def test_404_raises_not_found():
    with pytest.raises(NotFound):
        make_client(lambda request: httpx.Response(404, json={})).get("/missing")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, headers={"x-ratelimit-remaining": "0"}),
        httpx.Response(403, headers={"retry-after": "60"}),
        httpx.Response(429),
    ],
)
def test_rate_limits_are_detected(response):
    with pytest.raises(RateLimited):
        make_client(lambda request: response).get("/r")


def test_forbidden_without_rate_limit_headers_is_a_plain_error():
    with pytest.raises(GitHubError) as caught:
        make_client(lambda request: httpx.Response(403)).get("/r")
    assert not isinstance(caught.value, RateLimited)


def test_server_errors_are_retried_then_succeed():
    responses = iter([httpx.Response(502), httpx.Response(200, json={"ok": True})])
    assert make_client(lambda request: next(responses)).get("/r") == {"ok": True}


def test_server_errors_give_up_after_retries():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(503)

    with pytest.raises(GitHubError):
        make_client(handler, retries=2).get("/r")
    assert len(attempts) == 3


def test_network_errors_are_retried():
    responses = iter([httpx.ConnectError("down"), httpx.Response(200, json={"ok": True})])

    def handler(request):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    assert make_client(handler).get("/r") == {"ok": True}
