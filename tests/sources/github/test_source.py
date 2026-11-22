"""Tests for the GitHub source (sources/github/source.py) against a fake GitHub API."""

from datetime import UTC, datetime

import httpx
import pytest

from second_source.core.model import Claim
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.source import GitHubSource, PullRequestNotFound
from second_source.sources.github.urls import PullRequestRef

REF = PullRequestRef("Octo", "App", 7)
NOW = datetime(2026, 11, 21, tzinfo=UTC)


def make_source(fake):
    client = GitHubClient(transport=httpx.MockTransport(fake), sleep=lambda _s: None)
    return GitHubSource(client, clock=lambda: NOW)


def claim(claim_type, **attributes):
    return Claim(
        id=f"c-{claim_type}",
        episode_id="octo/app#7",
        type=claim_type,
        made_at=NOW,
        attributes=attributes,
    )


def test_load_reads_pull_commits_and_comments(fake_github):
    fake = fake_github()
    loaded = make_source(fake).load(REF)
    assert loaded.merged is True
    assert loaded.signals.head_branch == "codex/fix-parser"
    assert [t.origin for t in loaded.texts] == ["title", "body", "commit", "commit", "comment"]
    assert len(fake.requested) == 3  # PR, commits, comments


def test_missing_pull_request_is_a_clear_error(fake_github):
    with pytest.raises(PullRequestNotFound, match="Octo/App#8"):
        make_source(fake_github()).load(PullRequestRef("Octo", "App", 8))


def test_only_needed_evidence_is_fetched(fake_github):
    fake = fake_github()
    source = make_source(fake)
    loaded = source.load(REF)
    fake.requested.clear()
    result = source.fetch_evidence(loaded, [claim("TESTS_PASS", ref="c1")], {"check_result"})
    assert fake.requested == [
        "/repos/Octo/App/commits/c1/check-runs",
        "/repos/Octo/App/commits/c1/status",
    ]
    assert {(r.external_ref, r.version) for r in result.records} == {
        ("c1/pytest", 205),
        ("c1/ruff", 206),
    }
    assert result.stale == set()


def test_inaccessible_issue_is_skipped_not_stale(fake_github):
    source = make_source(fake_github({"/repos/Octo/App/issues/12": None}))
    loaded = source.load(REF)
    result = source.fetch_evidence(
        loaded, [claim("FIXES_ISSUE", issue=12, repo="")], {"issue_state"}
    )
    assert result.records == []
    assert result.stale == set()


def test_issues_of_a_renamed_repository_carry_its_current_name(fake_github):
    # The URL still names the old repository (GitHub redirects), but the episode uses the
    # current name, so the issue record must too or fixes_issue would not find it.
    routes = fake_github().routes
    pull = routes["/repos/Octo/App/pulls/7"]
    base = {**pull["base"], "repo": {**pull["base"]["repo"], "full_name": "Octo/NewApp"}}
    fake = fake_github(
        {
            "/repos/Octo/App/pulls/7": {**pull, "base": base},
            "/repos/Octo/NewApp/issues/12": routes["/repos/Octo/App/issues/12"],
            "/repos/Octo/NewApp/issues/12/events": [],
        }
    )
    source = make_source(fake)
    result = source.fetch_evidence(
        source.load(REF), [claim("FIXES_ISSUE", issue=12, repo="")], {"issue_state"}
    )
    assert [r.attributes["repo"] for r in result.records] == ["octo/newapp"]


def test_rate_limit_marks_this_and_all_later_types_stale(fake_github):
    limited = httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    source = make_source(fake_github({"/repos/Octo/App/commits/c1/check-runs": limited}))
    loaded = source.load(REF)
    result = source.fetch_evidence(
        loaded,
        [claim("TESTS_PASS", ref="c1")],
        {"pr_state", "check_result", "changed_file"},
    )
    assert [r.record_type for r in result.records] == ["pr_state"]  # fetched before the limit
    assert result.stale == {"check_result", "changed_file"}
    assert "rate limit" in result.errors[0]
    assert result.rate_limited


def test_other_errors_mark_only_their_own_type_stale(fake_github):
    broken = httpx.Response(500)
    source = make_source(fake_github({"/repos/Octo/App/pulls/7/files": broken}))
    loaded = source.load(REF)
    result = source.fetch_evidence(
        loaded, [claim("TESTS_PASS", ref="c1")], {"check_result", "changed_file"}
    )
    assert result.stale == {"changed_file"}
    assert any(r.record_type == "check_result" for r in result.records)
    assert not result.rate_limited  # stale, but not for lack of quota


def test_unknown_evidence_types_are_stale(fake_github):
    source = make_source(fake_github())
    result = source.fetch_evidence(source.load(REF), [], {"mystery"})
    assert result.stale == {"mystery"}


def test_reverts_are_not_looked_up_for_unmerged_pulls(fake_github):
    pull = dict(fake_github().routes["/repos/Octo/App/pulls/7"], merged_at=None, state="open")
    fake = fake_github({"/repos/Octo/App/pulls/7": pull})
    source = make_source(fake)
    loaded = source.load(REF)
    fake.requested.clear()
    assert source.fetch_evidence(loaded, [], {"revert"}).records == []
    assert fake.requested == []


def test_revert_scan_is_limited_to_the_durability_window(fake_github):
    fake = fake_github()
    source = make_source(fake)
    source.fetch_evidence(source.load(REF), [], {"revert"})
    (request,) = [r for r in fake.requests if r.url.path == "/repos/Octo/App/commits"]
    assert request.url.params["since"] == "2026-11-20T12:00:00Z"
    assert request.url.params["until"] == "2026-12-20T12:00:00Z"  # merge + 30 days


def test_revert_scan_lists_only_commits_touching_one_changed_file(fake_github):
    fake = fake_github()
    source = make_source(fake)
    source.fetch_evidence(source.load(REF), [], {"revert", "changed_file"})
    (request,) = [r for r in fake.requests if r.url.path == "/repos/Octo/App/commits"]
    assert request.url.params["path"] == "src/parser.py"
    files = [r for r in fake.requests if r.url.path == "/repos/Octo/App/pulls/7/files"]
    assert len(files) == 1  # shared by the changed_file and revert fetchers


def test_reverts_are_not_scanned_for_merges_into_another_branch(fake_github):
    pull = fake_github().routes["/repos/Octo/App/pulls/7"]
    base = {"ref": "feature/x", "repo": {"full_name": "Octo/App", "default_branch": "main"}}
    fake = fake_github({"/repos/Octo/App/pulls/7": {**pull, "base": base}})
    source = make_source(fake)
    loaded = source.load(REF)
    fake.requested.clear()
    assert source.fetch_evidence(loaded, [], {"revert"}).records == []
    assert fake.requested == []
