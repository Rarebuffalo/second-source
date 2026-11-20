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
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from second_source.packs.coding.extract import TextSource, extract_claims

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


def predict(text: str) -> Counter[str]:
    """Claim types extracted from one PR body."""
    source = TextSource(origin="body", text=text, written_at=_WRITTEN_AT)
    return Counter(claim.type for claim in extract_claims("golden", [source]))


def score(cases: Iterable[GoldenCase]) -> Score:
    result = Score()
    for case in cases:
        expected = Counter(case.expected)
        predicted = predict(case.text)
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
