"""Benchmark statistics: per-agent, per-claim-type truth rates with Wilson intervals (section 6).

Definitions (claims are counted from the batch runner's result records):

- **Counted per PR, not per claim.** A PR's claims of one type become one outcome (any
  contradicted → contradicted; else any verified → verified; else scoped CI failure; else
  pending; else unverifiable). One PR with 25 similar commit-message claims would otherwise
  outweigh 24 other PRs, and claims of one PR are not independent trials.
- **all** = a PR's stated claims together. The implicit MERGE_HOLDS claim (made by every merged
  PR, not written by the agent) is reported on its own and left out of **all**, so it cannot
  dilute the rate of claims agents actually wrote.
- Every cell reports how many **repositories** its checkable PRs come from: PRs of one
  repository are not independent either, so a rate resting on few repositories is weak.
- **checkable** PRs are those with a definite answer: VERIFIED or CONTRADICTED.
- **false rate** = contradicted / checkable, the share of checkable PRs whose claim was false.
- **coverage** = checkable / all PRs with the claim, so a low false rate cannot hide behind
  "almost nothing could be checked".
- **Only high-confidence claims count.** Claims found by extraction rules that have not proven
  their precision on the golden set (``confidence: low``) are counted separately, per agent, and
  never enter the rates. The implicit MERGE_HOLDS claim needs no extraction and always counts.
- **CI failed after a scoped claim** (reasons ``CI_FAILED_LOCAL_CLAIM`` and
  ``CI_FAILED_SUBSET_CLAIM``) is counted on its own and never folded into the false rate: the
  agent said a check passed *locally*, or that *part* of the checks passed, and a CI job
  failed. That is a signal, not proof (see docs/benchmark.md, section 4).

Intervals are 95% Wilson score intervals. Unlike the usual "p ± 1.96·SE", they stay inside
[0, 1] and remain sensible for small samples and for rates near 0 or 1.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from second_source.benchmark.aidev import repo_of

Z_95 = 1.959963984540054  # two-sided 95% normal quantile
SCOPED_CI_FAILED = frozenset({"CI_FAILED_LOCAL_CLAIM", "CI_FAILED_SUBSET_CLAIM"})
ALL = "all"  # pseudo claim type: every claim an agent wrote (not MERGE_HOLDS)
IMPLICIT_TYPES = frozenset({"MERGE_HOLDS"})  # claims no agent wrote; never part of ALL

# How a PR's several claims of one type combine into one outcome, strongest first.
_OUTCOME_ORDER = ("CONTRADICTED", "VERIFIED", "SCOPED_CI_FAILED", "PENDING", "UNVERIFIABLE")


def wilson_interval(successes: int, trials: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. (0, 1) when there are no trials."""
    if trials < 0 or not 0 <= successes <= trials:
        raise ValueError(f"invalid counts: {successes} of {trials}")
    if trials == 0:
        return 0.0, 1.0
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    margin = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    # At p = 0 or p = 1 the matching bound is exactly 0 or 1; set it directly so floating-point
    # rounding cannot report 0.9999... for "every claim was false".
    low = 0.0 if successes == 0 else max(0.0, centre - margin)
    high = 1.0 if successes == trials else min(1.0, centre + margin)
    return low, high


def outcome_of(result: str, reasons: Iterable[str]) -> str:
    """One claim's verdict as a counting outcome (scoped CI failures kept apart)."""
    if result == "UNVERIFIABLE" and SCOPED_CI_FAILED.intersection(reasons):
        return "SCOPED_CI_FAILED"
    return result if result in _OUTCOME_ORDER else "UNVERIFIABLE"


def combine(outcomes: Iterable[str]) -> str:
    """One outcome for several claims of the same type on one PR."""
    return min(outcomes, key=_OUTCOME_ORDER.index)


@dataclass
class Cell:
    """Counts of PRs for one agent and one claim type."""

    verified: int = 0
    contradicted: int = 0
    scoped_ci_failed: int = 0
    pending: int = 0
    unverifiable: int = 0  # excluding scoped_ci_failed
    repos: set[str] = field(default_factory=set)  # repositories of the checkable PRs

    def add(self, outcome: str, repo: str = "") -> None:
        if outcome == "VERIFIED":
            self.verified += 1
        elif outcome == "CONTRADICTED":
            self.contradicted += 1
        elif outcome == "PENDING":
            self.pending += 1
        elif outcome == "SCOPED_CI_FAILED":
            self.scoped_ci_failed += 1
        else:
            self.unverifiable += 1
        if outcome in ("VERIFIED", "CONTRADICTED") and repo:
            self.repos.add(repo)

    @property
    def total(self) -> int:
        return (
            self.verified
            + self.contradicted
            + self.scoped_ci_failed
            + self.pending
            + self.unverifiable
        )

    @property
    def checkable(self) -> int:
        return self.verified + self.contradicted

    @property
    def false_rate(self) -> float | None:
        return self.contradicted / self.checkable if self.checkable else None

    @property
    def coverage(self) -> float | None:
        return self.checkable / self.total if self.total else None

    def to_dict(self) -> dict[str, Any]:
        low, high = wilson_interval(self.contradicted, self.checkable)
        return {
            "prs": self.total,
            "repos": len(self.repos),
            "verified": self.verified,
            "contradicted": self.contradicted,
            "ci_failed_after_scoped_claim": self.scoped_ci_failed,
            "pending": self.pending,
            "unverifiable": self.unverifiable,
            "checkable": self.checkable,
            "coverage": self.coverage,
            "false_rate": self.false_rate,
            "false_rate_ci95": [low, high] if self.checkable else None,
        }


@dataclass
class AgentStats:
    prs: int = 0
    errors: int = 0
    detected_as_dataset_agent: int = 0
    low_confidence_claims: int = 0  # found by unproven rules; not in the rates
    claims: dict[str, Cell] = field(default_factory=lambda: defaultdict(Cell))


def summarize(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate runner result records into the published summary structure."""
    agents: dict[str, AgentStats] = defaultdict(AgentStats)
    for record in records:
        stats = agents[record["dataset_agent"]]
        if "error" in record:
            stats.errors += 1
            continue
        report = record["report"]
        stats.prs += 1
        detected = (report.get("agent") or {}).get("name")
        if detected == record["dataset_agent"]:
            stats.detected_as_dataset_agent += 1
        repo = repo_of(record["url"])
        by_type: dict[str, list[str]] = defaultdict(list)
        for claim in report["claims"]:
            if claim.get("attributes", {}).get("confidence", "high") != "high":
                stats.low_confidence_claims += 1
                continue
            by_type[claim["type"]].append(outcome_of(claim["result"], claim["reasons"]))
        for kind, outcomes in by_type.items():
            stats.claims[kind].add(combine(outcomes), repo)
        written = [
            o for kind, outcomes in by_type.items() if kind not in IMPLICIT_TYPES for o in outcomes
        ]
        if written:
            stats.claims[ALL].add(combine(written), repo)

    return {
        "agents": {
            agent: {
                "prs": stats.prs,
                "errors": stats.errors,
                "detection_agreement": (
                    stats.detected_as_dataset_agent / stats.prs if stats.prs else None
                ),
                "low_confidence_claims": stats.low_confidence_claims,
                "claims": {kind: cell.to_dict() for kind, cell in sorted(stats.claims.items())},
            }
            for agent, stats in sorted(agents.items())
        }
    }


# ---- Markdown -----------------------------------------------------------------------------


def _percent(value: float | None) -> str:
    return "–" if value is None else f"{100 * value:.0f}%"


def to_markdown(summary: Mapping[str, Any], *, title: str = "Agent Claim Truth") -> str:
    lines = [
        f"# {title}",
        "",
        "Counted per PR: a PR's claims of one type give one outcome. False rate = contradicted",
        "÷ checkable PRs (95% Wilson interval; PRs of one repository are not independent, so",
        "check the *Repos* column). Coverage = checkable ÷ all PRs with the claim. *Scoped, CI",
        "failed* = the agent said a check passed locally, or that part of the checks passed, and",
        "a CI job failed; shown separately and never counted as false. *all* = PRs by their",
        "written claims together: a PR counts as contradicted if any of them was, so it is not",
        "comparable to the per-type rates (MERGE_HOLDS, implicit in every merge, is left out).",
        "Only high-confidence claims (found by extraction rules with proven precision) are",
        "counted.",
        "",
    ]
    for agent, data in summary["agents"].items():
        agreement = _percent(data["detection_agreement"])
        lines += [
            f"## {agent}",
            "",
            f"{data['prs']} PRs checked, {data['errors']} errors, "
            f"agent detection agreed with the dataset label on {agreement}; "
            f"{data['low_confidence_claims']} low-confidence claims not counted.",
            "",
            "| Claim | PRs | Repos | Verified | Contradicted | Scoped, CI failed | Coverage | "
            "False rate (95% CI) |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        for kind, cell in data["claims"].items():
            interval = cell["false_rate_ci95"]
            rate = (
                "–"
                if interval is None
                else f"{_percent(cell['false_rate'])} "
                f"({_percent(interval[0])}–{_percent(interval[1])})"
            )
            lines.append(
                f"| {kind} | {cell['prs']} | {cell['repos']} | {cell['verified']} | "
                f"{cell['contradicted']} | "
                f"{cell['ci_failed_after_scoped_claim']} | {_percent(cell['coverage'])} | {rate} |"
            )
        lines.append("")
    return "\n".join(lines)
