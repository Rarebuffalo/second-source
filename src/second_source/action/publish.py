"""Publish a PR's verdicts as one "Second Source" check run, plus the job summary.

The check run is created once per head commit and then updated in place, so later runs (CI
finished, daily sweep, a recheck command) replace the table instead of piling up checks.

Conclusion rules (docs/action.md, section 4):

- CI still running for a claim -> the check stays ``in_progress``, so people see it is not done.
- a high-confidence claim is contradicted -> ``neutral`` by default (advice, never blocks a
  merge) or ``failure`` when the repository opts in with ``fail-on-contradiction``.
- otherwise -> ``success``, or ``neutral`` when there was nothing to check.

Low-confidence claims are shown in the table but never change the conclusion.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from second_source.core.model import Claim, Result
from second_source.pipeline import PullRequestReport
from second_source.report.render import WORDS, to_markdown
from second_source.sources.github.client import GitHubClient

CHECK_NAME = "Second Source"
DISPUTED_LABEL = "second-source: disputed"
MAX_SUMMARY = 65_000  # GitHub limits check run output text to 65,535 characters


@dataclass(frozen=True)
class Outcome:
    status: str  # "in_progress" or "completed"
    conclusion: str | None  # set only when completed
    title: str


def _counts(claim: Claim) -> bool:
    """Claims that decide the conclusion: high confidence, or implicit (no extraction)."""
    return claim.attributes.get("confidence", "high") == "high"


def awaiting_ci(report: PullRequestReport) -> bool:
    """True while a deciding claim is pending only because CI has not finished."""
    return any(
        verdict.result is Result.PENDING and "AWAITING_CI" in verdict.reasons
        for verdict in (report.verdict_for(c) for c in report.claims if _counts(c))
    )


def outcome_for(report: PullRequestReport, *, fail_on_contradiction: bool) -> Outcome:
    deciding = [report.verdict_for(c) for c in report.claims if _counts(c)]
    results = Counter(v.result for v in deciding)

    if results[Result.CONTRADICTED]:
        n = results[Result.CONTRADICTED]
        conclusion = "failure" if fail_on_contradiction else "neutral"
        return Outcome("completed", conclusion, f"{n} claim{'s' if n > 1 else ''} contradicted")
    if awaiting_ci(report):
        return Outcome("in_progress", None, "Waiting for CI")
    if not report.claims:
        return Outcome("completed", "neutral", "No claims found")

    order = [Result.VERIFIED, Result.PENDING, Result.UNVERIFIABLE]
    parts = [f"{results[r]} {WORDS[r]}" for r in order if results[r]]
    if not parts:
        return Outcome("completed", "neutral", "Only low-confidence claims")
    return Outcome("completed", "success", ", ".join(parts))


def summary_markdown(report: PullRequestReport) -> str:
    text = to_markdown(report)
    if DISPUTED_LABEL in report.labels:
        text = (
            "> **Disputed.** Someone on this PR believes a verdict is wrong; treat the results "
            "below with care.\n\n" + text
        )
    if len(text) > MAX_SUMMARY:
        text = text[:MAX_SUMMARY] + "\n\n_(truncated)_"
    return text


def publish_check_run(client: GitHubClient, report: PullRequestReport, outcome: Outcome) -> int:
    """Create or update the check run on the PR's head commit; return its id."""
    base = f"/repos/{report.ref.owner}/{report.ref.repo}"
    body: dict[str, object] = {
        "name": CHECK_NAME,
        "status": outcome.status,
        "output": {"title": outcome.title, "summary": summary_markdown(report)},
    }
    if outcome.conclusion is not None:
        body["conclusion"] = outcome.conclusion

    existing = client.get_paginated(
        f"{base}/commits/{report.head_sha}/check-runs", {"check_name": CHECK_NAME}, max_pages=1
    )
    if existing:
        run_id = int(existing[0]["id"])
        client.patch(f"{base}/check-runs/{run_id}", body)
        return run_id
    created = client.post(f"{base}/check-runs", {**body, "head_sha": report.head_sha})
    return int(created["id"])


def write_job_summary(path: Path | None, report: PullRequestReport, outcome: Outcome) -> None:
    """Append this PR's table to the workflow run's summary page (no-op outside Actions)."""
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"## {report.ref}: {outcome.title}\n\n{summary_markdown(report)}\n\n")
