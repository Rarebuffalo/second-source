"""End-to-end tests for the Action entrypoint (action/main.py) against the fake GitHub API."""

import io
import json
from datetime import UTC, datetime

import httpx

from second_source.action.main import EXIT_BAD_INPUT, EXIT_OK, run_action
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
