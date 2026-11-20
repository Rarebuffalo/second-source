"""Score claim extraction against a labelled golden set (docs/benchmark.md, section 3).

A golden case is a PR text plus the claim types a labeller says it asserts. Scoring compares
expected and predicted claim types per case as multisets (a PR can fix two issues), then sums
true positives, false positives and false negatives per claim type.

Real cases are split into **dev** (rules may be tuned on it) and **test** (only reported) by a
hash of the PR URL, so the split never depends on file order and never changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from second_source.benchmark.stats import wilson_interval
from second_source.packs.coding.extract import TextSource, extract_claims, rule_hits

DEV, TEST = "dev", "test"
_WRITTEN_AT = datetime(2026, 1, 1, tzinfo=UTC)  # extraction needs a time; its value is irrelevant


def split_for(url: str) -> str:
    """Deterministic 50/50 dev/test split from the PR URL."""
    return DEV if hashlib.sha256(url.encode()).digest()[0] % 2 == 0 else TEST


@dataclass(frozen=True)
class GoldenCase:
    name: str  # a URL for real cases, a short description for seed cases
    text: str
    expected: tuple[str, ...]
    agent: str = ""
    notes: str = ""

    @property
    def split(self) -> str:
        return split_for(self.name)


@dataclass
class Metrics:
    """Counts for one claim type, with precision and recall."""

    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0

    @property
    def precision(self) -> float:
        predicted = self.true_positive + self.false_positive
        return self.true_positive / predicted if predicted else 1.0

    @property
    def recall(self) -> float:
        return self.true_positive / self.support if self.support else 1.0

    @property
    def support(self) -> int:
        """How many claims of this type the labels contain."""
        return self.true_positive + self.false_negative


@dataclass
class Score:
    per_type: dict[str, Metrics] = field(default_factory=dict)
    mistakes: list[str] = field(default_factory=list)  # one line per case that went wrong

    def report(self) -> str:
        lines = ["Per-type results:"]
        for claim_type in sorted(self.per_type):
            m = self.per_type[claim_type]
            lines.append(
                f"  {claim_type:<13} precision={m.precision:.2f} recall={m.recall:.2f} "
                f"n={m.support} (tp={m.true_positive} fp={m.false_positive} "
                f"fn={m.false_negative})"
            )
        if self.mistakes:
            lines += ["Mismatched cases:", *self.mistakes]
        return "\n".join(lines)


_REPO_IN_URL = re.compile(r"github\.com/([^/]+/[^/]+)/pull/")


def predict(text: str, url: str = "") -> Counter[str]:
    """Claim types extracted from one PR body. The repository is taken from the PR URL, as the
    pipeline does, so "Fixes #12" and "Fixes owner/repo#12" count as one claim."""
    found = _REPO_IN_URL.search(url)
    source = TextSource(origin="body", text=text, written_at=_WRITTEN_AT)
    claims = extract_claims("golden", [source], repo=found.group(1) if found else None)
    return Counter(claim.type for claim in claims)


def score(cases: Iterable[GoldenCase]) -> Score:
    result = Score()
    for case in cases:
        expected = Counter(case.expected)
        predicted = predict(case.text, case.name)
        for claim_type in expected | predicted:
            metrics = result.per_type.setdefault(claim_type, Metrics())
            hits = min(expected[claim_type], predicted[claim_type])
            metrics.true_positive += hits
            metrics.false_positive += predicted[claim_type] - hits
            metrics.false_negative += expected[claim_type] - hits
        if expected != predicted:
            result.mistakes.append(
                f"  {case.name}: expected {dict(expected)}, got {dict(predicted)}"
            )
    return result


def load_cases(path: Path) -> list[GoldenCase]:
    """Read real golden cases from JSON Lines (one case per line)."""
    cases = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                cases.append(_case(json.loads(line)))
    return cases


def _case(record: dict[str, Any]) -> GoldenCase:
    return GoldenCase(
        name=record["url"],
        text=record["body"],
        expected=tuple(record["expected"]),
        agent=record.get("agent", ""),
        notes=record.get("notes", ""),
    )


def in_split(cases: Sequence[GoldenCase], split: str) -> list[GoldenCase]:
    return [case for case in cases if case.split == split]


# ---- building a golden set from two independent labellers ---------------------------------


@dataclass
class MergeResult:
    """Merged golden records plus what a reader needs to judge the labels."""

    records: list[dict[str, Any]]
    exact_agreement: int  # PRs where both labellers gave the same multiset of claims
    total: int
    unresolved: list[dict[str, Any]]  # disagreements without an adjudication yet


def merge_labels(
    rows: Sequence[Mapping[str, Any]],
    labels_a: Mapping[str, Mapping[str, Any]],
    labels_b: Mapping[str, Mapping[str, Any]],
    adjudications: Mapping[str, Mapping[str, Any]],
) -> MergeResult:
    """Combine two labellers' labels for sampled PRs (rows have url, agent and body).

    Where the labellers agree, their label is ``expected``. Where they disagree, an
    adjudication ``{"expected": [...], "reason": "..."}`` decides; without one the case is
    reported as unresolved and left out, so nothing is ever guessed.
    """
    urls = {row["url"] for row in rows}
    if set(labels_a) != urls or set(labels_b) != urls:
        raise ValueError("both labellers must label exactly the sampled PRs")

    records, unresolved, agreed = [], [], 0
    for row in rows:
        url = row["url"]
        a, b = sorted(labels_a[url]["expected"]), sorted(labels_b[url]["expected"])
        record: dict[str, Any] = {
            "url": url,
            "agent": row["agent"],
            "body": row["body"],
            "labeller_a": a,
            "labeller_b": b,
            "notes_a": labels_a[url].get("notes", ""),
            "notes_b": labels_b[url].get("notes", ""),
        }
        if a == b:
            agreed += 1
            record["expected"] = a
        elif url in adjudications:
            record["expected"] = sorted(adjudications[url]["expected"])
            record["adjudication"] = adjudications[url]["reason"]
        else:
            unresolved.append(record)
            continue
        records.append(record)
    return MergeResult(records, agreed, len(rows), unresolved)


# ---- precision per extraction rule ----------------------------------------------------------


@dataclass
class RuleMetrics:
    """How often one rule's hits were right, counted once per case it fired in."""

    claim_type: str
    correct: int = 0
    wrong: int = 0

    @property
    def hits(self) -> int:
        return self.correct + self.wrong

    @property
    def precision(self) -> float:
        return self.correct / self.hits if self.hits else 1.0

    @property
    def precision_low(self) -> float:
        """Lower end of the 95% Wilson interval: what the precision is at least, plausibly."""
        return wilson_interval(self.correct, self.hits)[0] if self.hits else 0.0


RuleKey = tuple[str, str]  # (claim type, rule name): command rules yield several claim types


def score_rules(cases: Iterable[GoldenCase]) -> dict[RuleKey, RuleMetrics]:
    """Precision of every extraction rule for each claim type it produces, judged on its own.

    A rule's hit in a case is correct when the case's labels contain that claim type. Each
    rule counts at most once per case and type, matching how claims are counted.
    """
    metrics: dict[RuleKey, RuleMetrics] = {}
    for case in cases:
        expected = set(case.expected)
        for claim_type, rule in rule_hits(case.text):
            entry = metrics.setdefault((claim_type, rule), RuleMetrics(claim_type))
            if claim_type in expected:
                entry.correct += 1
            else:
                entry.wrong += 1
    return metrics


def rules_report(metrics: Mapping[RuleKey, RuleMetrics]) -> str:
    lines = [f"  {'rule':<30} {'type':<13} hits  right  precision  (95% low)"]
    for (_kind, rule), m in sorted(metrics.items()):
        lines.append(
            f"  {rule:<30} {m.claim_type:<13} {m.hits:>4}  {m.correct:>5}  "
            f"{m.precision:>9.2f}  ({m.precision_low:.2f})"
        )
    return "\n".join(lines)
