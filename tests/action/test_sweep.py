"""Tests for the daily sweep (action/sweep.py)."""

from datetime import UTC, datetime

import httpx

from second_source.action.sweep import MAX_COMMIT_LOOKUPS, recent_pull_requests, rotated
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.urls import PullRequestRef

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


def client_serving(pages, commits=None):
    """Serves the PR list by page, and each PR's commits (none unless given)."""
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/commits"):
            number = int(request.url.path.split("/")[-2])
            return httpx.Response(200, json=(commits or {}).get(number, []))
        page = int(request.url.params["page"])
        return httpx.Response(200, json=pages[page - 1] if page <= len(pages) else [])

    return GitHubClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None), requests


def test_finds_recent_agent_prs_and_stops_at_the_cutoff():
    client, requests = client_serving([PULLS])
    refs = recent_pull_requests(client, "octo/app", now=NOW, days=30)
    assert [ref.number for ref in refs] == [5, 3]
    assert requests[0].url.params["sort"] == "updated"
    # One listing, plus the commits of the one PR that showed no agent itself (#4).
    assert [r.url.path for r in requests[1:]] == ["/repos/octo/app/pulls/4/commits"]


def test_an_agent_known_only_from_a_commit_trailer_is_swept():
    trailer = {"commit": {"message": "Fix\n\nCo-Authored-By: Claude <noreply@anthropic.com>"}}
    client, _ = client_serving([PULLS], commits={4: [trailer]})
    refs = recent_pull_requests(client, "octo/app", now=NOW, days=30)
    assert [ref.number for ref in refs] == [5, 4, 3]


def test_commit_lookups_are_capped():
    humans = [pull(900 - n, "2026-11-20T00:00:00Z") for n in range(MAX_COMMIT_LOOKUPS + 5)]
    client, requests = client_serving([humans])
    recent_pull_requests(client, "octo/app", now=NOW, days=30)
    assert sum(r.url.path.endswith("/commits") for r in requests) == MAX_COMMIT_LOOKUPS


def test_can_include_human_prs():
    client, _ = client_serving([PULLS])
    refs = recent_pull_requests(client, "octo/app", now=NOW, days=30, only_agents=False)
    assert [ref.number for ref in refs] == [5, 4, 3]


def test_reads_further_pages_only_while_prs_are_recent():
    full_page = [pull(1000 - n, "2026-11-20T00:00:00Z", branch="codex/x") for n in range(100)]
    client, requests = client_serving([full_page, PULLS])
    refs = recent_pull_requests(client, "octo/app", now=NOW, days=30)
    listings = [r for r in requests if not r.url.path.endswith("/commits")]
    assert len(refs) == 102 and len(listings) == 2


def test_each_days_sweep_starts_elsewhere_and_covers_every_pr():
    refs = [PullRequestRef("o", "r", n) for n in range(10)]
    starts = {rotated(refs, day)[0].number for day in range(10)}
    assert len(starts) == 10  # over ten days every PR is first once
    assert sorted(r.number for r in rotated(refs, 3)) == list(range(10))
    assert rotated([], 3) == []


def test_rotation_covers_every_pr_even_when_the_stride_divides_the_count():
    refs = [PullRequestRef("o", "r", n) for n in range(37)]
    assert {rotated(refs, day)[0].number for day in range(37)} == set(range(37))
