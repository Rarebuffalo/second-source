"""``second-source action``: the GitHub Action's entrypoint (docs/action.md).

One run: read inputs and the triggering event, plan what to do, then for each target PR
check it, wait for CI if needed, and publish the check run and job summary.

Failures on one PR (a deleted PR, a GitHub error, the rate limit) are reported as workflow
warnings and never fail the workflow: Second Source is advice, and a broken advisor must not
block anyone's merge. Only bad inputs fail the run, so misconfiguration is noticed.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from second_source.action.commands import CommandName, apply_dispute, may_run
from second_source.action.event import Mode, Plan, plan_for
from second_source.action.inputs import ActionInputs, InputError, read_context, read_inputs
from second_source.action.publish import (
    Outcome,
    outcome_for,
    publish_check_run,
    write_job_summary,
)
from second_source.action.sweep import recent_pull_requests
from second_source.action.wait import wait_for_ci
from second_source.pipeline import PullRequestReport, check_pull_request
from second_source.sources.github.client import GitHubClient, GitHubError
from second_source.sources.github.source import GitHubSource, PullRequestNotFound
from second_source.sources.github.urls import PullRequestRef

EXIT_OK = 0
EXIT_BAD_INPUT = 2


def run_action(
    env: Mapping[str, str],
    *,
    client_factory: Callable[[str], GitHubClient] = lambda token: GitHubClient(token),
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    out: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    try:
        inputs = read_inputs(env)
        context = read_context(env)
    except InputError as error:
        print(f"::error::{error}", file=out)
        return EXIT_BAD_INPUT

    plan = plan_for(context.event_name, context.event, context.repository)
    if plan.mode is Mode.SKIP:
        print(f"Nothing to do: {plan.note}.", file=out)
        return EXIT_OK

    with client_factory(inputs.token) as client:
        run = ActionRun(client, inputs, plan, context.summary_path, now, sleep, clock, out)
        refs, wait_seconds = run.targets(context.repository)
        for ref in refs:
            try:
                run.process(ref, wait_seconds)
            except PullRequestNotFound:
                print(f"::warning::{ref} was not found or is not accessible.", file=out)
            except GitHubError as error:
                print(f"::warning::could not check {ref}: {error}", file=out)
    return EXIT_OK


@dataclass
class ActionRun:
    """Everything one Action run shares across the PRs it checks."""

    client: GitHubClient
    inputs: ActionInputs
    plan: Plan
    summary_path: Path | None
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    clock: Callable[[], float]
    out: TextIO

    def targets(self, repository: str) -> tuple[list[PullRequestRef], float]:
        """Which PRs to check, and how long to wait for CI on each."""
        plan, out = self.plan, self.out
        if plan.mode is Mode.SWEEP:
            refs = recent_pull_requests(
                self.client,
                repository,
                now=self.now(),
                days=self.inputs.sweep_days,
                only_agents=self.inputs.only_agents,
            )
            print(f"Sweep: re-checking {len(refs)} recent agent pull request(s).", file=out)
            return refs, 0.0  # the sweep never waits; the next event or sweep picks CI up

        if plan.mode is Mode.COMMAND:
            command = plan.command
            assert command is not None
            if not may_run(command, plan.pr_author):
                print(f"Ignoring /second-source {command.name} from {command.author}.", file=out)
                return [], 0.0
            if command.name is CommandName.DISPUTE:
                apply_dispute(self.client, plan.refs[0], command)
            return list(plan.refs), 0.0  # re-check now, so the check run shows the change

        return list(plan.refs), self.inputs.wait_minutes * 60.0

    def process(self, ref: PullRequestRef, wait_seconds: float) -> None:
        """Check one PR (waiting for CI within the budget) and publish the result."""
        source = GitHubSource(self.client, clock=self.now)

        def check() -> PullRequestReport:
            return check_pull_request(ref, source, now=self.now())

        def outcome(report: PullRequestReport) -> Outcome:
            return outcome_for(report, fail_on_contradiction=self.inputs.fail_on_contradiction)

        report = check()
        # Commands are explicit requests, so they run even on PRs with no detected agent.
        if self.inputs.only_agents and report.agent is None and self.plan.mode is not Mode.COMMAND:
            print(f"{ref}: no coding agent detected, skipped.", file=self.out)
            return

        report = wait_for_ci(
            check,
            budget_seconds=wait_seconds,
            on_update=lambda current: publish_check_run(self.client, current, outcome(current)),
            sleep=self.sleep,
            clock=self.clock,
            first=report,
        )
        final = outcome(report)
        publish_check_run(self.client, report, final)
        write_job_summary(self.summary_path, report, final)
        print(f"{ref}: {final.title}", file=self.out)
