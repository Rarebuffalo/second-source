"""Render a PullRequestReport for people (terminal, Markdown) and for programs (JSON).

All three renderers share ``rows()``, so the terminal, the GitHub check and the JSON output
always say the same thing about each claim.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from second_source.core.model import Claim, Result, Verdict
from second_source.pipeline import PullRequestReport

JSON_SCHEMA_VERSION = "2"  # 2: caveats, withdrawn, check roles, opened_at

SYMBOLS = {
    Result.VERIFIED: "✔",
    Result.CONTRADICTED: "✘",
    Result.PENDING: "…",
    Result.UNVERIFIABLE: "?",
}
WORDS = {
    Result.VERIFIED: "verified",
    Result.CONTRADICTED: "contradicted",
    Result.PENDING: "pending",
    Result.UNVERIFIABLE: "unverifiable",
}
_ANSI = {
    Result.VERIFIED: "\033[32m",  # green
    Result.CONTRADICTED: "\033[31m",  # red
    Result.PENDING: "\033[33m",  # yellow
    Result.UNVERIFIABLE: "\033[90m",  # grey
}
_RESET = "\033[0m"

# Reasons shown in words: what the integrity guards found (docs/packs/coding.md, "Integrity
# guards"), and absences that are not proof.
GUARD_TEXT = {
    "PR_SKIPS_TESTS": "this PR skips tests",
    "PR_WEAKENS_TEST_COMMAND": "this PR weakens how CI runs tests",
    "PR_LOWERS_COVERAGE_GATE": "this PR lowers the coverage gate",
    "ADDED_TESTS_ARE_SKIPPED": "the added tests are skipped",
    "PR_CHANGES_CI_CONFIG": "this PR changed CI or test configuration",
    "PR_REMOVES_TESTS": "this PR removes tests while the code stays",
    "LINT_SUPPRESSIONS_ADDED": "this PR adds lint suppressions",
    "LINT_CONFIG_CHANGED": "this PR changed lint configuration",
    "MATCHING_JOB_SKIPPED": "some matching CI jobs were skipped",
    "CI_TOLERATES_TEST_FAILURES": "this PR lets CI continue when tests fail",
    "BUILD_SKIPS_TESTS": "this PR makes a build command skip tests",
    "NOT_INSPECTED": "GitHub's API left out the diff of some files, so they were not inspected",
    "GUARD_ERROR": "an integrity guard could not be evaluated",
    # Absence that is not proof (never CONTRADICTED; see the check files for why).
    "NO_TEST_FILES_FOUND": "no test file found in the changed files (tests inside code files"
    " are not detected)",
    "NO_DOC_FILES_FOUND": "no documentation file found in the changed files (docs inside code"
    " are not detected)",
    "REOPENED_THEN_CLOSED": "the issue was reopened after the merge, then closed again",
    "REVERT_UNDONE": "the merge was reverted, then the revert was itself reverted",
}
# Every other reason in words, for people reading a check run; the JSON report keeps the
# codes. A reason missing here is shown as its code (tests keep this table complete).
REASON_TEXT = {
    "ALL_MATCHING_CI_JOBS_PASSED": "every matching CI job passed",
    "AWAITING_CI": "waiting for CI to finish",
    "CI_JOB_FAILED": "a matching CI job failed",
    "CI_FAILED_LOCAL_CLAIM": "CI failed, but the claim was about a local run",
    "CI_FAILED_SUBSET_CLAIM": "CI failed, but the claim covered only part of the checks",
    "CLAIM_COMMIT_AMBIGUOUS": "CI failed on only one of the commits the claim could mean",
    "CI_TIMEOUT": "CI did not finish in time",
    "NO_CI_RECORDS": "no CI ran on this commit (or its results were deleted)",
    "NO_CI_RESULTS": "the matching CI jobs were skipped, cancelled or timed out",
    "NO_MATCHING_CI_JOBS": "CI ran, but no job matched this claim",
    "NO_COMMIT_REFERENCE": "no commit to check it against",
    "SOURCE_STALE": "GitHub's data could not be read",
    "TEST_FILES_CHANGED": "test files changed",
    "DOC_FILES_CHANGED": "documentation files changed",
    "NO_FILE_LIST": "the changed files could not be read",
    "ISSUE_CLOSED_BY_MERGE": "the issue was closed when the PR merged",
    "ISSUE_ALREADY_CLOSED": "the issue was already closed before the PR",
    "ISSUE_STILL_OPEN": "the issue is still open after the merge",
    "ISSUE_OPEN": "the issue is open",
    "ISSUE_REOPENED": "the issue was reopened after the merge",
    "ISSUE_STAYED_CLOSED": "the issue stayed closed",
    "ISSUE_NOT_ACCESSIBLE": "the issue could not be read",
    "NOT_AUTO_CLOSED": "GitHub does not close issues referenced this way",
    "NOT_DEFAULT_BRANCH": "merged into a branch other than the default",
    "NO_CLOSE_TIME": "GitHub gave no close time for the issue",
    "NO_MERGE_TIME": "GitHub gave no merge time",
    "NO_PR_STATE": "the PR's state could not be read",
    "NOT_MERGED": "the PR was not merged",
    "PR_NOT_MERGED": "the PR was closed without merging",
    "NOT_MERGED_YET": "decided once the PR is merged",
    "CLOSE_PENDING": "merged; waiting for GitHub to close the issue",
    "IN_WINDOW": "still being watched",
    "NOT_REVERTED": "not reverted",
    "REVERTED": "the merge was reverted",
    "DEPLOYMENT_SUCCEEDED": "a deployment of this commit succeeded",
    "DEPLOYMENT_FAILED": "every deployment of this commit failed",
    "DEPLOYMENT_FAILED_UNCLEAR": "a deployment failed, but GitHub may not list them all",
    "DEPLOYMENT_UNCLEAR": "the deployments do not settle it",
    "DEPLOYING": "a deployment is still running",
    "NO_DEPLOYMENT_RECORDS": "no deployment of this commit was found",
}
WITHDRAWN_SYMBOL, WITHDRAWN_WORD = "⚠", "not verified"

# Pending reasons whose code alone misleads a reader: after the merge, "Fixes #3: pending
# (IN_WINDOW)" reads as if the issue were still open. Keyed by claim type and reason; the JSON
# report keeps the codes.
PENDING_TEXT = {
    ("FIXES_ISSUE", "IN_WINDOW"): "issue closed; watching for a reopen",
    ("MERGE_HOLDS", "IN_WINDOW"): "not reverted so far; watching for a revert",
    ("FIXES_ISSUE", "NOT_MERGED_YET"): "decided once the PR is merged",
    ("FIXES_ISSUE", "CLOSE_PENDING"): "merged; waiting for GitHub to close the issue",
}


def guard_text(reason: str) -> str:
    return GUARD_TEXT.get(reason) or REASON_TEXT.get(reason, reason)


def withdrawn_reasons(verdict: Verdict) -> tuple[str, ...]:
    """Reasons of guards that withdrew a verification: the deciding checks all said VERIFIED
    and only guards made the claim UNVERIFIABLE. Empty for every other verdict, including a
    claim that was already unverifiable for its own reasons (CI stale, no matching job)."""
    if verdict.result is not Result.UNVERIFIABLE:
        return ()
    guard_reasons = {
        run.reason
        for run in verdict.check_runs
        if run.role == "guard" and run.result is Result.UNVERIFIABLE
    }
    other_reasons = {
        run.reason
        for run in verdict.check_runs
        if run.role != "guard" and run.result is not Result.VERIFIED
    }
    if not verdict.reasons or any(
        reason not in guard_reasons or reason in other_reasons for reason in verdict.reasons
    ):
        return ()
    return verdict.reasons


# Display order: problems first.
_ORDER = [Result.CONTRADICTED, Result.UNVERIFIABLE, Result.PENDING, Result.VERIFIED]


@dataclass(frozen=True)
class Row:
    """One claim with its verdict, prepared for display."""

    label: str
    result: Result
    reasons: tuple[str, ...]
    details: tuple[str, ...]  # one explanation per check run
    origin: str
    span: str | None
    ref: str | None
    withdrawn: tuple[str, ...] = ()  # guard reasons that withdrew a verification
    cautions: tuple[str, ...] = ()  # optional guard reasons shown next to the result
    claim_type: str = ""

    @property
    def symbol(self) -> str:
        return WITHDRAWN_SYMBOL if self.withdrawn else SYMBOLS[self.result]

    @property
    def word(self) -> str:
        return WITHDRAWN_WORD if self.withdrawn else WORDS[self.result]

    @property
    def why(self) -> str:
        """Reasons, with guard findings in words and cautions after them."""
        parts = [self._reason_text(r) for r in self.reasons]
        parts += [f"caution: {guard_text(c)}" for c in self.cautions]
        return ", ".join(parts)

    def _reason_text(self, reason: str) -> str:
        if self.result is Result.PENDING and (self.claim_type, reason) in PENDING_TEXT:
            return PENDING_TEXT[(self.claim_type, reason)]
        return guard_text(reason)


def describe_claim(claim: Claim) -> str:
    """A short human description of a claim, e.g. "Fixes #12" or "Deployed to staging"."""
    attributes = claim.attributes
    match claim.type:
        case "TESTS_PASS":
            return "Tests pass"
        case "BUILD_OK":
            return "Build succeeds"
        case "LINT_OK":
            return "Lint passes"
        case "TESTS_ADDED":
            return "Tests added"
        case "DOCS_UPDATED":
            return "Docs updated"
        case "FIXES_ISSUE":
            repo = attributes.get("repo") or ""
            return f"Fixes {repo}#{attributes.get('issue')}"
        case "DEPLOYED":
            return f"Deployed to {attributes.get('environment')}"
        case "MERGE_HOLDS":
            return "Merged change holds (not reverted)"
        case _:
            return claim.type


def rows(report: PullRequestReport) -> list[Row]:
    prepared = []
    for claim in report.claims:
        verdict: Verdict = report.verdict_for(claim)
        label = describe_claim(claim)
        if claim.attributes.get("confidence") == "low":
            # Found by an extraction rule that has not proven its precision on the golden set:
            # the agent may not have meant this as a claim.
            label += " (low confidence)"
        prepared.append(
            Row(
                label=label,
                result=verdict.result,
                reasons=verdict.reasons,
                details=tuple(
                    f"{run.check_id}: {run.explanation or run.reason}"
                    for run in verdict.check_runs
                    # A guard that found nothing is noise in the details.
                    if not (run.role == "guard" and run.result is Result.VERIFIED)
                ),
                origin=claim.origin,
                span=claim.span,
                ref=claim.attributes.get("ref"),
                withdrawn=withdrawn_reasons(verdict),
                cautions=verdict.caveats,
                claim_type=claim.type,
            )
        )
    return sorted(prepared, key=lambda row: _ORDER.index(row.result))


def summary_counts(report: PullRequestReport) -> Counter[Result]:
    return Counter(verdict.result for verdict in report.verdicts)


def _agent_line(report: PullRequestReport) -> str:
    if report.agent is None:
        return "Agent: not detected (no known coding-agent signal)"
    match = report.agent
    return f"Agent: {match.agent} (detected by {match.signal.value}: {match.detail})"


def _summary_line(report: PullRequestReport) -> str:
    counts = summary_counts(report)
    parts = [f"{counts[result]} {WORDS[result]}" for result in _ORDER if counts[result]]
    return ", ".join(parts) if parts else "no claims found"


# ---- terminal --------------------------------------------------------------------------------


def to_text(report: PullRequestReport, *, color: bool = False) -> str:
    def paint(result: Result, text: str) -> str:
        return f"{_ANSI[result]}{text}{_RESET}" if color else text

    lines = [
        f"Second Source · {report.ref} · {report.ref.url}",
        _agent_line(report),
        f"Claims: {_summary_line(report)}",
        "",
    ]
    for row in rows(report):
        lines.append(paint(row.result, f"{row.symbol} {row.word:<12} {row.label}"))
        if row.span:
            lines.append(f'    said in {row.origin}: "{printable(row.span)}"')
        lines.append(f"    why: {row.why}")
        lines.extend(f"    - {detail}" for detail in row.details)
    if report.stale:
        lines += ["", f"Not fully checked (data unavailable): {', '.join(sorted(report.stale))}"]
    lines += ["", f"GitHub API requests: {report.api_requests}"]
    return "\n".join(lines)


# ---- Markdown (GitHub check runs and job summaries) ------------------------------------------


# C0 and C1 control characters (except tab) and bidirectional overrides: in a terminal they can
# rewrite the screen or the clipboard; in Markdown they can hide or reorder text.
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


def printable(text: str) -> str:
    """PR text with control and bidi-override characters replaced (security review 1, L2)."""
    return _UNSAFE.sub("\ufffd", text)


_CROSS_REPO_REF = re.compile(r"\S+/\S+#[0-9]+")


def code_span(text: str) -> str:
    """PR text as an inline code span: GitHub renders it literally, so HTML, links, images and
    @-mentions in it stay inert (security review 1, M1). The fence is longer than any run of
    backticks inside, as CommonMark requires."""
    text = printable(text).replace("\n", " ")
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    return f"{fence} {text} {fence}"


def escape_markdown_cell(text: str) -> str:
    """Escape text for a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


_OWN_CHECKS = frozenset({"Second Source", "second-source"})


def evidence_digest(report: PullRequestReport) -> list[str]:
    """What GitHub's own record says about this PR, whatever the agent claimed: CI on the head
    commit, what the change touched, merge and revert state. Shown on every report, so the
    check is useful on PRs that make no claims too (product review 1, F3).

    Only evidence that was fetched is described; a line is left out rather than guessed.
    """
    records = report.evidence
    lines: list[str] = []
    if "check_result" in report.fetched and "check_result" not in report.stale:
        jobs = [
            e.attributes
            for e in records
            if e.record_type == "check_result"
            and e.attributes.get("ref") == report.head_sha
            and e.attributes.get("name") not in _OWN_CHECKS
        ]
        if not jobs:
            lines.append("CI on the head commit: no CI results (yet)")
        else:
            running = sum(1 for j in jobs if j.get("status") != "completed")
            failed = sorted(j["name"] for j in jobs if j.get("conclusion") == "failure")
            passed = sum(1 for j in jobs if j.get("conclusion") == "success")
            parts = [f"{passed} passed"]
            if failed:
                shown = ", ".join(failed[:5]) + (" …" if len(failed) > 5 else "")
                parts.append(f"{len(failed)} failed ({shown})")
            if running:
                parts.append(f"{running} still running")
            others = len(jobs) - passed - len(failed) - running
            if others:
                parts.append(f"{others} skipped, cancelled or neutral")
            lines.append(f"CI on the head commit: {', '.join(parts)}")
    summary = next(
        (e.attributes for e in records if e.record_type == "changed_files_summary"), None
    )
    if summary is not None:
        touched = [
            f"{summary.get('files', 0)} file(s) changed",
            "test files changed" if summary.get("test_source_changed") else "no test files changed",
        ]
        if summary.get("ci_config_changed"):
            touched.append("**CI configuration changed**")
        if summary.get("removed_test_files"):
            touched.append(f"{summary['removed_test_files']} test file(s) removed")
        if summary.get("lint_suppressions_added"):
            touched.append("lint suppressions added")
        lines.append("Change: " + "; ".join(touched))
    pr = next((e.attributes for e in records if e.record_type == "pr_state"), None)
    if pr is not None:
        if pr.get("merged"):
            reverts = [
                e for e in records if e.record_type == "revert" and not e.attributes.get("undone")
            ]
            watch = "reverted" if reverts else "no revert found so far"
            lines.append(f"Merged; {watch}")
        else:
            lines.append("Not merged" + (" (closed)" if pr.get("state") == "closed" else ""))
    return [f"- {line}" for line in lines]


def to_markdown(report: PullRequestReport) -> str:
    lines = [
        f"**Second Source** checked the claims in {report.ref}.",
        "",
        _agent_line(report),
        "",
        f"**Claims:** {_summary_line(report)}",
        "",
    ]
    table = rows(report)
    if table:
        lines += ["| | Claim | Result | Why | Said in |", "|---|---|---|---|---|"]
        for row in table:
            said = f"{row.origin}: {code_span(row.span)}" if row.span else row.origin
            # A repository named in "Fixes owner/name#12" comes from PR text, and a name such
            # as "a/_www.example.com" would render as a live link: show it as code
            # (security review 2, N8).
            label = escape_markdown_cell(_CROSS_REPO_REF.sub(lambda m: code_span(m[0]), row.label))
            why = escape_markdown_cell(row.why)
            lines.append(
                f"| {row.symbol} | {label} | {row.word} | {why} | {escape_markdown_cell(said)} |"
            )
    digest = evidence_digest(report)
    if digest:
        while lines and lines[-1] == "":
            lines.pop()  # the claims line already ends a paragraph when there is no table
        lines += ["", "**Evidence at a glance** (from GitHub, whatever was claimed):", "", *digest]
    if report.stale:
        lines += ["", f"_Not fully checked (data unavailable): {', '.join(sorted(report.stale))}_"]
    lines += [
        "",
        "Missing or unavailable evidence is reported as *unverifiable*, never as contradicted.",
        "*Low confidence* marks claims found by text patterns whose accuracy is not yet proven.",
        "*Not verified* (⚠) means CI passed but the same PR changed what passing means (skipped",
        "tests, a weakened test command); a *caution* is shown without changing the result.",
    ]
    return "\n".join(lines)


# ---- JSON ------------------------------------------------------------------------------------


def to_json(report: PullRequestReport) -> dict[str, Any]:
    """A stable, versioned structure for scripts. Times are ISO 8601 UTC."""
    claims = []
    for claim in report.claims:
        verdict = report.verdict_for(claim)
        claims.append(
            {
                "id": claim.id,
                "type": claim.type,
                "label": describe_claim(claim),
                "origin": claim.origin,
                "made_at": claim.made_at.isoformat(),
                "span": claim.span,
                "attributes": {k: _plain(v) for k, v in claim.attributes.items()},
                "result": verdict.result.value,
                "reasons": list(verdict.reasons),
                "withdrawn": list(withdrawn_reasons(verdict)),
                "caveats": list(verdict.caveats),
                "checks": [
                    {
                        "id": run.check_id,
                        "version": run.check_version,
                        "level": run.level.value,
                        "role": run.role,
                        "result": run.result.value,
                        "reason": run.reason,
                        "explanation": run.explanation,
                        "inputs_hash": run.inputs_hash,
                    }
                    for run in verdict.check_runs
                ],
            }
        )
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "pull_request": {
            "ref": str(report.ref),
            "url": report.ref.url,
            "opened_at": report.episode.opened_at.isoformat(),
            # The repository's current name (a renamed one still answers at its old URL).
            "repo": report.episode.attributes.get("repo"),
        },
        "agent": (
            None
            if report.agent is None
            else {"name": report.agent.agent, "signal": report.agent.signal.value}
        ),
        "checked_at": report.checked_at.isoformat(),
        "summary": {WORDS[result]: count for result, count in summary_counts(report).items()},
        "claims": claims,
        "stale": sorted(report.stale),
        "errors": list(report.errors),
        "api_requests": report.api_requests,
        "fetched": sorted(report.fetched),
    }


def evidence_snapshot(report: PullRequestReport) -> list[dict[str, Any]]:
    """The evidence records behind the verdicts, JSON-safe and compact.

    Stored with benchmark results so a verdict stays auditable after GitHub deletes the source
    data (old workflow runs expire; repositories get deleted or renamed).
    """
    snapshot = []
    for record in report.evidence:
        item: dict[str, Any] = {
            "type": record.record_type,
            "ref": record.external_ref,
            "attributes": {key: _plain(value) for key, value in record.attributes.items()},
            # Kept so a re-score de-duplicates as the original run did (newest version wins).
            "observed_at": record.observed_at.isoformat(),
            "version": record.version,
        }
        if record.occurred_at is not None:
            item["occurred_at"] = record.occurred_at.isoformat()
        snapshot.append(item)
    return snapshot


def _plain(value: Any) -> Any:
    """Make attribute values JSON-safe (datetimes become ISO strings)."""
    return value.isoformat() if hasattr(value, "isoformat") else value
