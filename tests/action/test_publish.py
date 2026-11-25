"""Tests for check-run publishing and conclusions (action/publish.py)."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from second_source.action.publish import (
    CHECK_NAME,
    DISPUTED_LABEL,
    Outcome,
    outcome_for,
    publish_check_run,
    summary_markdown,
    write_job_summary,
)
from second_source.pipeline import check_pull_request
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.source import GitHubSource
from second_source.sources.github.urls import PullRequestRef

REF = PullRequestRef("Octo", "App", 7)
PR = "/repos/Octo/App/pulls/7"
DAY_AFTER = datetime(2026, 11, 21, tzinfo=UTC)


def report_for(fake, now=DAY_AFTER):
    client = GitHubClient(transport=httpx.MockTransport(fake), sleep=lambda _s: None)
    return check_pull_request(REF, GitHubSource(client, clock=lambda: now), now=now)


def with_body(fake_github, body, **overrides):
    pull = {**fake_github.BASE_ROUTES[PR], "body": body}
    return fake_github({PR: pull, **overrides})


# ---- conclusions ---------------------------------------------------------------------------


def test_verified_and_watched_claims_succeed(fake_github):
    outcome = outcome_for(report_for(fake_github()), fail_on_contradiction=False)
    # "Tests added" is verified; "Fixes #12" and the merge are inside their watch window.
    assert outcome == Outcome("completed", "success", "1 verified, 2 pending")


def test_contradicted_claims_are_neutral_unless_the_repo_opts_in(fake_github):
    still_open = {"/repos/Octo/App/issues/12": {"number": 12, "state": "open"}}
    report = report_for(fake_github(still_open))  # "Fixes #12" but the issue is still open
    advice = outcome_for(report, fail_on_contradiction=False)
    gate = outcome_for(report, fail_on_contradiction=True)
    assert (advice.conclusion, advice.title) == ("neutral", "1 claim contradicted")
    assert gate.conclusion == "failure"


def test_running_ci_keeps_the_check_in_progress(fake_github):
    running = {
        "/repos/Octo/App/commits/c1/check-runs": {
            "total_count": 1,
            "check_runs": [{"id": 1, "name": "pytest", "status": "in_progress"}],
        }
    }
    fake = with_body(fake_github, "All tests pass.", **running)
    outcome = outcome_for(
        report_for(fake, datetime(2026, 11, 20, 9, 30, tzinfo=UTC)), fail_on_contradiction=False
    )
    assert outcome == Outcome("in_progress", None, "Waiting for CI")


def test_ci_failing_behind_an_unverifiable_claim_is_grey_not_green(fake_github):
    # Stage B (sandbox #12): "Tests pass locally." while CI failed. Not a contradiction, but a
    # green check would read as "claims hold".
    failing = {
        "/repos/Octo/App/commits/c1/check-runs": {
            "total_count": 1,
            "check_runs": [
                {"id": 1, "name": "pytest", "status": "completed", "conclusion": "failure"}
            ],
        }
    }
    report = report_for(with_body(fake_github, "Tests pass locally.", **failing))
    outcome = outcome_for(report, fail_on_contradiction=True)
    assert outcome.conclusion == "neutral" and outcome.title.endswith("(a CI job failed)")
    assert "contradicted" not in outcome.title


def test_low_confidence_contradictions_never_decide(fake_github):
    failing = {
        "/repos/Octo/App/commits/c1/check-runs": {
            "total_count": 1,
            "check_runs": [
                {"id": 1, "name": "pytest", "status": "completed", "conclusion": "failure"}
            ],
        }
    }
    report = report_for(fake_github(failing))  # "✅ `pytest -q`" is a low-confidence claim
    assert outcome_for(report, fail_on_contradiction=True).conclusion == "success"


def test_no_claims_is_neutral(fake_github):
    open_pr = {
        **fake_github.BASE_ROUTES[PR],
        "body": "Refactor.",
        "merged_at": None,
        "state": "open",
    }
    fake = fake_github({PR: open_pr, "/repos/Octo/App/issues/7/comments": []})
    outcome = outcome_for(report_for(fake), fail_on_contradiction=False)
    assert outcome == Outcome("completed", "neutral", "No claims found")


# ---- summary and publishing ----------------------------------------------------------------


def test_disputed_prs_say_so_in_the_summary(fake_github):
    pull = {**fake_github.BASE_ROUTES[PR], "labels": [{"name": DISPUTED_LABEL}]}
    assert summary_markdown(report_for(fake_github({PR: pull}))).startswith("> **Disputed.**")


class Recorder:
    """Wraps the fake GitHub: records writes and answers the check-run lookup."""

    def __init__(self, fake, existing):
        self.fake, self.existing, self.writes = fake, existing, []

    def __call__(self, request):
        if request.method in ("POST", "PATCH"):
            self.writes.append((request.method, request.url.path, json.loads(request.content)))
            return httpx.Response(201, json={"id": 77})
        # The lookup of our own check run (by name) on the head commit; CI evidence for the
        # same commit is fetched without a name and goes to the fake.
        if (
            request.url.path.endswith("/commits/c2/check-runs")
            and "check_name" in request.url.params
        ):
            assert request.url.params["check_name"] == CHECK_NAME
            return httpx.Response(
                200, json={"total_count": len(self.existing), "check_runs": self.existing}
            )
        return self.fake(request)


@pytest.mark.parametrize(
    ("existing", "method", "path", "run_id"),
    [
        ([], "POST", "/repos/Octo/App/check-runs", 77),
        (
            [{"id": 5, "app": {"slug": "github-actions"}}],
            "PATCH",
            "/repos/Octo/App/check-runs/5",
            5,
        ),
    ],
)
def test_check_run_is_created_once_then_updated(fake_github, existing, method, path, run_id):
    recorder = Recorder(fake_github(), existing)
    client = GitHubClient(transport=httpx.MockTransport(recorder), sleep=lambda _s: None)
    report = check_pull_request(REF, GitHubSource(client, clock=lambda: DAY_AFTER), now=DAY_AFTER)
    outcome = Outcome("completed", "success", "1 verified")
    assert publish_check_run(client, report, outcome) == run_id
    ((sent_method, sent_path, body),) = recorder.writes
    assert (sent_method, sent_path) == (method, path)
    assert body["name"] == CHECK_NAME and body["conclusion"] == "success"
    assert body["output"]["title"] == "1 verified"
    assert ("head_sha" in body) is (method == "POST")


def test_an_older_report_never_replaces_a_newer_one(fake_github):
    # Two runs on one PR: a run still waiting for CI must not overwrite the verdict that a
    # later run (triggered by CI finishing) already published.
    def client_for(existing):
        recorder = Recorder(fake_github(), existing)
        transport = httpx.MockTransport(recorder)
        return recorder, GitHubClient(transport=transport, sleep=lambda _s: None)

    newer = (DAY_AFTER + timedelta(minutes=5)).isoformat()
    ours = {"id": 5, "app": {"slug": "github-actions"}}
    recorder, client = client_for([{**ours, "external_id": newer}])
    report = check_pull_request(REF, GitHubSource(client, clock=lambda: DAY_AFTER), now=DAY_AFTER)
    assert publish_check_run(client, report, Outcome("in_progress", None, "Waiting")) == 5
    assert recorder.writes == []
    # An older or unreadable record is replaced, and the new one records its own time.
    for shown in ((DAY_AFTER - timedelta(minutes=5)).isoformat(), "", "junk", "2030-01-01"):
        recorder, client = client_for([{**ours, "external_id": shown}])
        publish_check_run(client, report, Outcome("completed", "success", "1 verified"))
        ((_method, _path, body),) = recorder.writes
        assert body["external_id"] == report.checked_at.isoformat()


def test_a_look_alike_check_run_is_never_adopted(fake_github):
    # Security review 1, M2: another app (or a workflow the PR edited) creates a "Second
    # Source" check with a far-future time, which would freeze the real check forever.
    def client_for(existing):
        recorder = Recorder(fake_github(), existing)
        transport = httpx.MockTransport(recorder)
        return recorder, GitHubClient(transport=transport, sleep=lambda _s: None)

    other_app = {"id": 9, "app": {"slug": "evil-app"}, "external_id": "2026-11-21T00:01:00+00:00"}
    recorder, client = client_for([other_app])
    report = check_pull_request(REF, GitHubSource(client, clock=lambda: DAY_AFTER), now=DAY_AFTER)
    publish_check_run(client, report, Outcome("completed", "success", "1 verified"))
    assert recorder.writes[0][0] == "POST"  # our own run, beside the look-alike
    future = {
        "id": 5,
        "app": {"slug": "github-actions"},
        "external_id": "9999-12-31T00:00:00+00:00",
    }
    recorder, client = client_for([future])
    publish_check_run(client, report, Outcome("completed", "success", "1 verified"), now=DAY_AFTER)
    assert recorder.writes[0][:2] == ("PATCH", "/repos/Octo/App/check-runs/5")


def test_in_progress_checks_send_no_conclusion(fake_github):
    recorder = Recorder(fake_github(), [])
    client = GitHubClient(transport=httpx.MockTransport(recorder), sleep=lambda _s: None)
    report = check_pull_request(REF, GitHubSource(client, clock=lambda: DAY_AFTER), now=DAY_AFTER)
    publish_check_run(client, report, Outcome("in_progress", None, "Waiting for CI"))
    assert "conclusion" not in recorder.writes[0][2]


def test_job_summary_is_appended(fake_github, tmp_path):
    path = tmp_path / "summary.md"
    report = report_for(fake_github())
    write_job_summary(path, report, Outcome("completed", "success", "1 verified"))
    write_job_summary(path, report, Outcome("completed", "success", "1 verified"))
    assert path.read_text().count("## Octo/App#7: 1 verified") == 2
    write_job_summary(None, report, Outcome("completed", "success", "x"))  # outside Actions


def test_a_withdrawn_claim_is_neutral_never_failure(fake_github):
    skipped = [
        {
            "filename": "tests/test_parser.py",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": "@@ -1,1 +1,2 @@\n+@pytest.mark.skip\n def test_empty():",
        },
    ]
    fake = with_body(fake_github, "All tests pass.", **{f"{PR}/files": skipped})
    report = report_for(fake)
    for opted_in in (False, True):
        outcome = outcome_for(report, fail_on_contradiction=opted_in)
        assert outcome == Outcome(
            "completed", "neutral", "1 claim not verified: this PR skips tests"
        )


def test_cautions_never_change_the_conclusion(fake_github):
    workflow = [
        {
            "filename": ".github/workflows/ci.yml",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": "@@ -1,1 +1,2 @@\n name: ci\n+on: [push]",
        },
        {
            "filename": "tests/test_parser.py",
            "status": "added",
            "additions": 9,
            "deletions": 0,
            "patch": "@@ -0,0 +1,2 @@\n+def test_empty():\n+    assert parse('') == []",
        },
    ]
    report = report_for(with_body(fake_github, "All tests pass.", **{f"{PR}/files": workflow}))
    assert outcome_for(report, fail_on_contradiction=False).conclusion == "success"


def test_the_job_summary_stays_under_githubs_limit(fake_github, tmp_path):
    path = tmp_path / "summary.md"
    path.write_text("x" * (999_000))
    report = report_for(fake_github())
    write_job_summary(path, report, Outcome("completed", "success", "1 verified"))
    assert path.stat().st_size <= 1_000_000
    assert path.read_text().endswith("- Octo/App#7: 1 verified\n")


def test_every_check_shows_the_evidence_at_a_glance(fake_github):
    # Product review 1, F3: most PRs make no claims; the check is useful on them too.
    from second_source.action.main import DIGEST_EVIDENCE

    fake = with_body(fake_github, "Small cleanup.")  # no claims at all
    client = GitHubClient(transport=httpx.MockTransport(fake), sleep=lambda _s: None)
    report = check_pull_request(
        REF,
        GitHubSource(client, clock=lambda: DAY_AFTER),
        now=DAY_AFTER,
        extra_evidence=DIGEST_EVIDENCE,
    )
    summary = summary_markdown(report)
    assert "**Evidence at a glance**" in summary
    assert "- CI on the head commit: 2 passed" in summary
    assert "file(s) changed" in summary and "test files changed" in summary
    assert "- Merged; no revert found so far" in summary
