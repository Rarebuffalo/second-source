"""End-to-end tests for the Action entrypoint (action/main.py) against the fake GitHub API."""

import io
import json
from datetime import UTC, datetime

import httpx

from second_source.action.main import EXIT_BAD_INPUT, EXIT_OK, own_workflow_path, run_action
from second_source.cli.main import main as cli_main
from second_source.sources.github.client import GitHubClient

NOW = datetime(2026, 11, 21, tzinfo=UTC)
PR = "/repos/Octo/App/pulls/7"


class Recorder:
    """The fake GitHub plus write recording and an empty check-run list on the head commit."""

    def __init__(self, fake):
        self.fake, self.writes = fake, []

    def __call__(self, request):
        if request.method in ("POST", "PATCH"):
            self.writes.append((request.method, request.url.path, json.loads(request.content)))
            return httpx.Response(201, json={"id": 77})
        if request.url.path == "/repos/Octo/App/commits/c2/check-runs":
            return httpx.Response(200, json={"total_count": 0, "check_runs": []})
        return self.fake(request)

    def paths(self):
        return [(method, path) for method, path, _body in self.writes]


def run(tmp_path, recorder, event_name, event, **inputs):
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event))
    env = {
        "GITHUB_REPOSITORY": "Octo/App",
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "INPUT_GITHUB_TOKEN": "token",
        "INPUT_WAIT_MINUTES": "0",
        **{f"INPUT_{k.upper()}": v for k, v in inputs.items()},
    }
    out = io.StringIO()
    code = run_action(
        env,
        client_factory=lambda token: GitHubClient(
            token, transport=httpx.MockTransport(recorder), sleep=lambda _s: None
        ),
        now=lambda: NOW,
        sleep=lambda _s: None,
        clock=lambda: 0.0,
        out=out,
    )
    return code, out.getvalue()


OPENED = {"action": "opened", "pull_request": {"number": 7}}


def test_pull_request_event_publishes_a_check_run_and_summary(fake_github, tmp_path):
    recorder = Recorder(fake_github())
    code, out = run(tmp_path, recorder, "pull_request_target", OPENED)
    assert code == EXIT_OK
    assert recorder.paths() == [("POST", "/repos/Octo/App/check-runs")]
    body = recorder.writes[0][2]
    assert body["head_sha"] == "c2" and body["conclusion"] == "success"
    assert "Octo/App#7: 1 verified, 2 pending" in out
    assert "## Octo/App#7" in (tmp_path / "summary.md").read_text()


def test_irrelevant_events_do_nothing(fake_github, tmp_path):
    recorder = Recorder(fake_github())
    code, out = run(tmp_path, recorder, "push", {})
    assert code == EXIT_OK and recorder.writes == [] and "Nothing to do" in out


def test_human_prs_are_skipped_by_default(fake_github, tmp_path):
    human = {**fake_github.BASE_ROUTES[PR], "head": {"ref": "feature", "sha": "c2"}}
    recorder = Recorder(fake_github({PR: human}))
    _code, out = run(tmp_path, recorder, "pull_request", OPENED)
    assert recorder.writes == [] and "no coding agent detected" in out
    recorder = Recorder(fake_github({PR: human}))
    run(tmp_path, recorder, "pull_request", OPENED, only_agents="false")
    assert recorder.paths() == [("POST", "/repos/Octo/App/check-runs")]


def test_sweep_rechecks_recent_agent_prs(fake_github, tmp_path):
    listing = [
        {
            "number": 7,
            "updated_at": "2026-11-20T12:00:00Z",
            "user": {"login": "someone"},
            "head": {"ref": "codex/fix-parser"},
            "body": "",
        },
    ]
    recorder = Recorder(fake_github({"/repos/Octo/App/pulls": listing}))
    code, out = run(tmp_path, recorder, "schedule", {})
    assert code == EXIT_OK and "re-checking 1 recent" in out
    assert recorder.paths() == [("POST", "/repos/Octo/App/check-runs")]


def listing_of(*numbers):
    return [
        {
            "number": n,
            "updated_at": "2026-11-20T12:00:00Z",
            "user": {"login": "someone"},
            "head": {"ref": "codex/fix-parser"},
            "body": "",
        }
        for n in numbers
    ]


class Limited(Recorder):
    """Every response says how many requests are left; optionally 403 at the limit."""

    def __init__(self, fake, remaining, *, exhausted=False):
        super().__init__(fake)
        self.remaining, self.exhausted = remaining, exhausted

    def __call__(self, request):
        if self.exhausted and request.url.path == PR:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
        response = super().__call__(request)
        response.headers["x-ratelimit-remaining"] = str(self.remaining)
        return response


def test_the_sweep_stops_while_requests_remain_for_pr_events(fake_github, tmp_path):
    recorder = Limited(fake_github({"/repos/Octo/App/pulls": listing_of(7, 8, 9)}), 150)
    code, out = run(tmp_path, recorder, "schedule", {})
    assert code == EXIT_OK and "Sweep stopped after 0 of 3" in out
    assert recorder.writes == []


def test_the_rate_limit_stops_the_run_without_failing_it(fake_github, tmp_path):
    fake = fake_github({"/repos/Octo/App/pulls": listing_of(7, 8)})
    code, out = run(tmp_path, Limited(fake, 900, exhausted=True), "schedule", {})
    assert code == EXIT_OK and out.count("rate limit was reached") == 1


def test_a_report_cut_short_by_the_rate_limit_is_not_published(fake_github, tmp_path):
    limited = httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    recorder = Recorder(fake_github({"/repos/Octo/App/actions/runs": limited}))
    code, out = run(tmp_path, recorder, "pull_request", OPENED)
    assert code == EXIT_OK and "rate limit was reached" in out
    assert recorder.writes == []  # nothing "unverifiable" published over a real result


def test_one_wait_budget_covers_every_pr_of_a_ci_run(fake_github, tmp_path):
    # A CI run that built two PRs: waiting for the first must not give the second a fresh
    # budget, or the job outlives its timeout.
    running = {  # CI still running on every commit, so each PR would wait its full budget
        "total_count": 1,
        "workflow_runs": [
            {"id": 1, "name": "CI", "status": "in_progress", "created_at": "2026-11-21T00:00:00Z"}
        ],
    }
    claiming = {**fake_github.BASE_ROUTES[PR], "body": "All tests pass."}  # high confidence
    pulls = {f"/repos/Octo/App/pulls/{n}": claiming for n in (7, 8)}
    recorder = Recorder(fake_github({"/repos/Octo/App/actions/runs": running, **pulls}))
    elapsed = [0.0]
    event_file = tmp_path / "event.json"
    event_file.write_text(
        json.dumps(
            {
                "action": "completed",
                "workflow_run": {"pull_requests": [{"number": 7}, {"number": 8}]},
            }
        )
    )
    env = {
        "GITHUB_REPOSITORY": "Octo/App",
        "GITHUB_EVENT_NAME": "workflow_run",
        "GITHUB_EVENT_PATH": str(event_file),
        "INPUT_GITHUB_TOKEN": "token",
        "INPUT_WAIT_MINUTES": "1",
    }
    run_action(
        env,
        client_factory=lambda token: GitHubClient(
            token, transport=httpx.MockTransport(recorder), sleep=lambda _s: None
        ),
        now=lambda: NOW,
        sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        clock=lambda: elapsed[0],
        out=io.StringIO(),
    )
    assert 0 < elapsed[0] <= 60


def test_a_failed_listing_warns_without_failing(fake_github, tmp_path):
    def broken(request):
        if request.url.path == "/repos/Octo/App/pulls":
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
        return fake_github()(request)

    code, out = run(tmp_path, Recorder(broken), "schedule", {})
    assert code == EXIT_OK and "::warning::could not list what to check" in out


def comment(body, association):
    return {
        "action": "created",
        "issue": {"number": 7, "user": {"login": "someone"}, "pull_request": {}},
        "comment": {"body": body, "user": {"login": "alice"}, "author_association": association},
    }


def test_dispute_from_a_member_labels_replies_and_rechecks(fake_github, tmp_path):
    recorder = Recorder(fake_github())
    run(tmp_path, recorder, "issue_comment", comment("/second-source dispute wrong job", "MEMBER"))
    assert recorder.paths() == [
        ("POST", "/repos/Octo/App/issues/7/labels"),
        ("POST", "/repos/Octo/App/issues/7/comments"),
        ("POST", "/repos/Octo/App/check-runs"),
    ]


def test_commands_from_strangers_are_ignored(fake_github, tmp_path):
    recorder = Recorder(fake_github())
    _code, out = run(tmp_path, recorder, "issue_comment", comment("/second-source recheck", "NONE"))
    assert recorder.writes == [] and "Ignoring /second-source recheck from alice" in out


def test_missing_prs_warn_without_failing(fake_github, tmp_path):
    recorder = Recorder(fake_github())
    event = {"action": "opened", "pull_request": {"number": 99}}
    code, out = run(tmp_path, recorder, "pull_request", event)
    assert code == EXIT_OK and "::warning::Octo/App#99 was not found" in out


def test_bad_inputs_fail_the_run(fake_github, tmp_path):
    code, out = run(tmp_path, Recorder(fake_github()), "pull_request", OPENED, wait_minutes="soon")
    assert code == EXIT_BAD_INPUT and "::error::input wait-minutes must be a number" in out


def test_the_cli_routes_to_the_action():
    out = io.StringIO()
    assert cli_main(["action"], env={}, stdout=out, stderr=io.StringIO()) == EXIT_BAD_INPUT
    assert "github-token is empty" in out.getvalue()


def test_the_actions_own_workflow_file_is_read_from_the_environment():
    ref = "Octo/App/.github/workflows/second-source.yml@refs/heads/main"
    assert own_workflow_path({"GITHUB_WORKFLOW_REF": ref}) == ".github/workflows/second-source.yml"
    assert own_workflow_path({}) is None


def test_a_dispute_that_github_refuses_says_so_and_still_rechecks(fake_github, tmp_path):
    # Found live on the sandbox: without pull-requests: write, labelling a PR is a 403.
    refused = httpx.Response(403, json={"message": "Resource not accessible by integration"})
    recorder = Recorder(fake_github({"/repos/Octo/App/issues/7/labels": refused}))
    code, out = run(
        tmp_path, recorder, "issue_comment", comment("/second-source dispute x", "MEMBER")
    )
    assert code == EXIT_OK and "::warning::could not record the dispute" in out
    assert ("POST", "/repos/Octo/App/check-runs") in recorder.paths()
