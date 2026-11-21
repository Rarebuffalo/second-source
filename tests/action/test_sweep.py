"""Tests for the daily sweep (action/sweep.py)."""

from datetime import UTC, datetime

import httpx

from second_source.action.sweep import recent_pull_requests
from second_source.sources.github.client import GitHubClient

NOW = datetime(2026, 11, 21, tzinfo=UTC)


def pull(number, updated, *, login="someone", branch="feature", body=""):
    return {
        "number": number,
        "updated_at": updated,
        "user": {"login": login},
        "head": {"ref": branch},
        "body": body,
    }


PULLS = [
    pull(5, "2026-11-20T10:00:00Z", branch="codex/fix"),  # agent by branch
    pull(4, "2026-11-19T10:00:00Z"),  # a human PR
    pull(3, "2026-11-10T10:00:00Z", body="🤖 Generated with [Claude Code](https://claude.com)"),
    pull(2, "2026-10-01T10:00:00Z", branch="codex/old"),  # older than the cutoff
    pull(1, "2026-09-01T10:00:00Z", branch="codex/older"),
]


def client_serving(pages):
    requests = []

    def handler(request):
        requests.append(request)
        page = int(request.url.params["page"])
        return httpx.Response(200, json=pages[page - 1] if page <= len(pages) else [])

    return GitHubClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None), requests


def test_finds_recent_agent_prs_and_stops_at_the_cutoff():
    client, requests = client_serving([PULLS])
    refs = recent_pull_requests(client, "octo/app", now=NOW, days=30)
    assert [ref.number for ref in refs] == [5, 3]
    assert requests[0].url.params["sort"] == "updated"
    assert len(requests) == 1


def test_can_include_human_prs():
    client, _ = client_serving([PULLS])
    refs = recent_pull_requests(client, "octo/app", now=NOW, days=30, only_agents=False)
    assert [ref.number for ref in refs] == [5, 4, 3]


def test_reads_further_pages_only_while_prs_are_recent():
    full_page = [pull(1000 - n, "2026-11-20T00:00:00Z", branch="codex/x") for n in range(100)]
    client, requests = client_serving([full_page, PULLS])
    refs = recent_pull_requests(client, "octo/app", now=NOW, days=30)
    assert len(refs) == 102 and len(requests) == 2
