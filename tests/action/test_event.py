"""Tests for turning workflow events into plans (action/event.py)."""

import pytest

from second_source.action.commands import CommandName
from second_source.action.event import Mode, plan_for
from second_source.sources.github.urls import PullRequestRef

REPO = "octo/app"


@pytest.mark.parametrize("event_name", ["pull_request", "pull_request_target"])
@pytest.mark.parametrize("action", ["opened", "edited", "synchronize", "closed"])
def test_pull_request_events_check_the_pr(event_name, action):
    plan = plan_for(event_name, {"action": action, "pull_request": {"number": 7}}, REPO)
    assert plan.mode is Mode.CHECK and plan.refs == (PullRequestRef("octo", "app", 7),)


def test_irrelevant_pull_request_actions_are_skipped():
    plan = plan_for("pull_request", {"action": "labeled", "pull_request": {"number": 7}}, REPO)
    assert plan.mode is Mode.SKIP and "labeled" in plan.note


def test_completed_workflow_runs_check_their_prs():
    event = {
        "action": "completed",
        "workflow_run": {"pull_requests": [{"number": 9}, {"number": 3}, {"number": 9}]},
    }
    plan = plan_for("workflow_run", event, REPO)
    assert plan.mode is Mode.CHECK
    assert [ref.number for ref in plan.refs] == [3, 9]


@pytest.mark.parametrize(
    "event",
    [
        {"action": "requested", "workflow_run": {"pull_requests": [{"number": 1}]}},
        {"action": "completed", "workflow_run": {"pull_requests": []}},  # e.g. a fork PR
    ],
)
def test_other_workflow_runs_are_skipped(event):
    assert plan_for("workflow_run", event, REPO).mode is Mode.SKIP


@pytest.mark.parametrize("event_name", ["schedule", "workflow_dispatch"])
def test_scheduled_runs_sweep(event_name):
    assert plan_for(event_name, {}, REPO).mode is Mode.SWEEP


def comment_event(body, *, on_pr=True, action="created"):
    issue = {"number": 7, "user": {"login": "pr-author"}}
    if on_pr:
        issue["pull_request"] = {"url": "..."}
    return {
        "action": action,
        "issue": issue,
        "comment": {"body": body, "user": {"login": "alice"}, "author_association": "MEMBER"},
    }


def test_pr_comments_with_a_command_become_commands():
    plan = plan_for("issue_comment", comment_event("/second-source dispute wrong job"), REPO)
    assert plan.mode is Mode.COMMAND
    assert plan.command.name is CommandName.DISPUTE and plan.command.argument == "wrong job"
    assert plan.pr_author == "pr-author" and plan.refs[0].number == 7


@pytest.mark.parametrize(
    "event",
    [
        comment_event("looks good"),
        comment_event("/second-source recheck", on_pr=False),  # an issue, not a PR
        comment_event("/second-source recheck", action="edited"),
    ],
)
def test_other_comments_are_skipped(event):
    assert plan_for("issue_comment", event, REPO).mode is Mode.SKIP


def test_unknown_events_are_skipped_with_a_note():
    plan = plan_for("push", {}, REPO)
    assert plan.mode is Mode.SKIP and "push" in plan.note
