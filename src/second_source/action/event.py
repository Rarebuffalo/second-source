"""Turn the event that triggered a workflow run into a plan: what to check, and how.

| Event                                    | Mode    |
|------------------------------------------|---------|
| pull_request / pull_request_target       | CHECK the PR                          |
| workflow_run (completed)                 | CHECK the PRs that run built          |
| schedule / workflow_dispatch             | SWEEP recent agent PRs                |
| issue_comment with a /second-source line | COMMAND on that PR                    |
| anything else                            | SKIP, with a note saying why          |
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from second_source.action.commands import Command, parse_command
from second_source.sources.github.urls import PullRequestRef

PR_EVENTS = frozenset({"pull_request", "pull_request_target"})
# PR actions after which the claims or the evidence may have changed.
PR_ACTIONS = frozenset(
    {"opened", "edited", "synchronize", "reopened", "ready_for_review", "closed"}
)


class Mode(StrEnum):
    CHECK = "check"
    SWEEP = "sweep"
    COMMAND = "command"
    SKIP = "skip"


@dataclass(frozen=True)
class Plan:
    mode: Mode
    refs: tuple[PullRequestRef, ...] = ()
    command: Command | None = None
    pr_author: str = ""  # for command permission checks
    note: str = ""  # why a run was skipped, shown in the log


def _ref(repository: str, number: int) -> PullRequestRef:
    owner, name = repository.split("/", 1)
    return PullRequestRef(owner, name, int(number))


def plan_for(event_name: str, event: dict[str, Any], repository: str) -> Plan:
    if event_name in PR_EVENTS:
        action = event.get("action", "")
        if action not in PR_ACTIONS:
            return Plan(Mode.SKIP, note=f"pull request action '{action}' changes nothing to check")
        return Plan(Mode.CHECK, (_ref(repository, event["pull_request"]["number"]),))

    if event_name == "workflow_run":
        run = event.get("workflow_run", {})
        if event.get("action") != "completed":
            return Plan(Mode.SKIP, note="workflow run has not completed")
        # GitHub lists the PRs whose head commit this run built. PRs from forks are not
        # listed; the daily sweep covers them.
        numbers = sorted({pr["number"] for pr in run.get("pull_requests", [])})
        if not numbers:
            return Plan(Mode.SKIP, note="the workflow run belongs to no pull request here")
        return Plan(Mode.CHECK, tuple(_ref(repository, n) for n in numbers))

    if event_name in ("schedule", "workflow_dispatch"):
        return Plan(Mode.SWEEP)

    if event_name == "issue_comment":
        issue = event.get("issue", {})
        comment = event.get("comment", {})
        if event.get("action") != "created" or "pull_request" not in issue:
            return Plan(Mode.SKIP, note="not a new comment on a pull request")
        command = parse_command(
            comment.get("body", ""),
            (comment.get("user") or {}).get("login", ""),
            comment.get("author_association", ""),
        )
        if command is None:
            return Plan(Mode.SKIP, note="the comment has no /second-source command")
        return Plan(
            Mode.COMMAND,
            (_ref(repository, issue["number"]),),
            command=command,
            pr_author=(issue.get("user") or {}).get("login", ""),
        )

    return Plan(Mode.SKIP, note=f"event '{event_name}' is not used by Second Source")
