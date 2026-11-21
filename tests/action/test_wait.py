"""Tests for waiting on CI (action/wait.py), with a fake clock and no real sleeping."""

from datetime import UTC, datetime

import httpx

from second_source.action.wait import wait_for_ci
from second_source.pipeline import check_pull_request
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.source import GitHubSource
from second_source.sources.github.urls import PullRequestRef

REF = PullRequestRef("Octo", "App", 7)
PR = "/repos/Octo/App/pulls/7"
RUNS = "/repos/Octo/App/commits/c1/check-runs"
EARLY = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)  # inside the CI timeout


def runs(status, conclusion=None):
    run = {"id": 1, "name": "pytest", "status": status}
    if conclusion:
        run["conclusion"] = conclusion
    return {"total_count": 1, "check_runs": [run]}


class FakeTime:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_check(fake_github, answers):
    """A check function whose CI results come from `answers`, one per call."""
    pull = {**fake_github.BASE_ROUTES[PR], "body": "All tests pass."}
    calls = []

    def check():
        fake = fake_github({PR: pull, RUNS: answers[min(len(calls), len(answers) - 1)]})
        calls.append(1)
        client = GitHubClient(transport=httpx.MockTransport(fake), sleep=lambda _s: None)
        return check_pull_request(REF, GitHubSource(client, clock=lambda: EARLY), now=EARLY)

    return check, calls


def result_of_tests_pass(report):
    return next(report.verdict_for(c) for c in report.claims if c.type == "TESTS_PASS").result.value


def test_waits_until_ci_finishes(fake_github):
    check, calls = make_check(
        fake_github, [runs("in_progress"), runs("in_progress"), runs("completed", "success")]
    )
    clock, updates = FakeTime(), []
    report = wait_for_ci(
        check,
        budget_seconds=600,
        on_update=updates.append,
        sleep=clock.sleep,
        clock=lambda: clock.now,
    )
    assert result_of_tests_pass(report) == "VERIFIED"
    assert len(calls) == 3 and len(updates) == 2
    assert clock.sleeps == [30.0, 60.0]  # backoff doubles


def test_stops_when_the_budget_runs_out(fake_github):
    check, calls = make_check(fake_github, [runs("in_progress")])
    clock = FakeTime()
    report = wait_for_ci(check, budget_seconds=100, sleep=clock.sleep, clock=lambda: clock.now)
    assert result_of_tests_pass(report) == "PENDING"
    assert clock.sleeps == [30.0, 60.0, 10.0]  # the last pause is cut to the remaining budget
    assert len(calls) == 4


def test_no_budget_means_a_single_check(fake_github):
    check, calls = make_check(fake_github, [runs("in_progress")])
    clock = FakeTime()
    wait_for_ci(check, budget_seconds=0, sleep=clock.sleep, clock=lambda: clock.now)
    assert len(calls) == 1 and clock.sleeps == []


def test_settled_reports_return_immediately(fake_github):
    check, calls = make_check(fake_github, [runs("completed", "success")])
    wait_for_ci(check, budget_seconds=600, sleep=FakeTime().sleep, clock=lambda: 0.0)
    assert len(calls) == 1


def test_an_existing_report_saves_the_first_check(fake_github):
    check, calls = make_check(fake_github, [runs("completed", "success")])
    first = check()
    wait_for_ci(check, budget_seconds=600, sleep=FakeTime().sleep, clock=lambda: 0.0, first=first)
    assert len(calls) == 1
