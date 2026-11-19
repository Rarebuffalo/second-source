"""End-to-end test of the pipeline on the shared fake agent PR (see tests/conftest.py)."""

from datetime import UTC, datetime

import httpx
import pytest

from second_source.core.model import Result
from second_source.pipeline import check_pull_request
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.source import GitHubSource, PullRequestNotFound
from second_source.sources.github.urls import PullRequestRef

REF = PullRequestRef("Octo", "App", 7)
DAY_AFTER = datetime(2026, 11, 21, tzinfo=UTC)
TWO_MONTHS_LATER = datetime(2027, 1, 20, tzinfo=UTC)


def run(fake, now):
    client = GitHubClient(transport=httpx.MockTransport(fake), sleep=lambda _s: None)
    return check_pull_request(REF, GitHubSource(client, clock=lambda: now), now=now)


def results(report):
    return {claim.type: report.verdict_for(claim).result for claim in report.claims}


def test_detects_agent_and_extracts_every_claim(fake_github):
    report = run(fake_github(), DAY_AFTER)
    assert report.agent.agent == "codex"
    assert sorted(c.type for c in report.claims) == [
        "DEPLOYED",
        "FIXES_ISSUE",
        "LINT_OK",
        "MERGE_HOLDS",
        "TESTS_ADDED",
        "TESTS_PASS",
    ]


def test_verdicts_the_day_after_merge(fake_github):
    assert results(run(fake_github(), DAY_AFTER)) == {
        "TESTS_PASS": Result.VERIFIED,  # pytest passed on re-run 205
        "LINT_OK": Result.VERIFIED,
        "TESTS_ADDED": Result.VERIFIED,
        "FIXES_ISSUE": Result.PENDING,  # closed at merge; still inside the reopen window
        "DEPLOYED": Result.UNVERIFIABLE,  # no GitHub deployment records: never an accusation
        "MERGE_HOLDS": Result.PENDING,  # no revert yet; still inside the window
    }


def test_durable_claims_settle_after_their_windows(fake_github):
    settled = results(run(fake_github(), TWO_MONTHS_LATER))
    assert settled["FIXES_ISSUE"] is Result.VERIFIED
    assert settled["MERGE_HOLDS"] is Result.VERIFIED


def test_a_failing_test_job_contradicts_the_claim(fake_github):
    failing = {
        "/repos/Octo/App/commits/c1/check-runs": {
            "total_count": 2,
            "check_runs": [
                {"id": 205, "name": "pytest", "status": "completed", "conclusion": "failure"},
                {"id": 206, "name": "ruff", "status": "completed", "conclusion": "success"},
            ],
        }
    }
    assert results(run(fake_github(failing), DAY_AFTER))["TESTS_PASS"] is Result.CONTRADICTED


def test_a_revert_contradicts_the_merge(fake_github):
    revert = [
        {
            "sha": "r1",
            "commit": {
                "message": "Revert\n\nThis reverts commit abc1234.",
                "committer": {"date": "2026-11-20T15:00:00Z"},
            },
        }
    ]
    report = run(fake_github({"/repos/Octo/App/commits": revert}), DAY_AFTER)
    assert results(report)["MERGE_HOLDS"] is Result.CONTRADICTED


def test_rate_limit_produces_unverifiable_not_contradicted(fake_github):
    limited = httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    report = run(fake_github({"/repos/Octo/App/commits/c1/check-runs": limited}), DAY_AFTER)
    assert results(report)["TESTS_PASS"] is Result.UNVERIFIABLE
    assert "check_result" in report.stale
    assert report.errors


def test_reports_api_usage(fake_github):
    assert run(fake_github(), DAY_AFTER).api_requests > 0


def test_missing_pull_request(fake_github):
    client = GitHubClient(transport=httpx.MockTransport(fake_github()), sleep=lambda _s: None)
    with pytest.raises(PullRequestNotFound):
        check_pull_request(PullRequestRef("Octo", "App", 99), GitHubSource(client), now=DAY_AFTER)
