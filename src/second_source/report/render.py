"""Render a PullRequestReport for people (terminal, Markdown) and for programs (JSON).

All three renderers share ``rows()``, so the terminal, the GitHub check and the JSON output
always say the same thing about each claim.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from second_source.core.model import Claim, Result, Verdict
from second_source.pipeline import PullRequestReport

JSON_SCHEMA_VERSION = "1"

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
        prepared.append(
            Row(
                label=describe_claim(claim),
                result=verdict.result,
                reasons=verdict.reasons,
                details=tuple(
                    f"{run.check_id}: {run.explanation or run.reason}" for run in verdict.check_runs
                ),
                origin=claim.origin,
                span=claim.span,
                ref=claim.attributes.get("ref"),
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
        lines.append(
            paint(row.result, f"{SYMBOLS[row.result]} {WORDS[row.result]:<12} {row.label}")
        )
        if row.span:
            lines.append(f'    said in {row.origin}: "{row.span}"')
        lines.append(f"    why: {', '.join(row.reasons)}")
        lines.extend(f"    - {detail}" for detail in row.details)
    if report.stale:
        lines += ["", f"Not fully checked (data unavailable): {', '.join(sorted(report.stale))}"]
    lines += ["", f"GitHub API requests: {report.api_requests}"]
    return "\n".join(lines)


# ---- Markdown (GitHub check runs and job summaries) ------------------------------------------


def escape_markdown_cell(text: str) -> str:
    """Escape text for a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


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
            said = f'{row.origin}: "{row.span}"' if row.span else row.origin
            label = escape_markdown_cell(row.label)
            why = escape_markdown_cell(", ".join(row.reasons))
            lines.append(
                f"| {SYMBOLS[row.result]} | {label} | {WORDS[row.result]} | {why} | "
                f"{escape_markdown_cell(said)} |"
            )
    if report.stale:
        lines += ["", f"_Not fully checked (data unavailable): {', '.join(sorted(report.stale))}_"]
    lines += [
        "",
        "Missing or unavailable evidence is reported as *unverifiable*, never as contradicted.",
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
                "span": claim.span,
                "attributes": {k: _plain(v) for k, v in claim.attributes.items()},
                "result": verdict.result.value,
                "reasons": list(verdict.reasons),
                "checks": [
                    {
                        "id": run.check_id,
                        "version": run.check_version,
                        "level": run.level.value,
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
        "pull_request": {"ref": str(report.ref), "url": report.ref.url},
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
    }


def _plain(value: Any) -> Any:
    """Make attribute values JSON-safe (datetimes become ISO strings)."""
    return value.isoformat() if hasattr(value, "isoformat") else value
