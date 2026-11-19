"""Golden-set gate for claim extraction: precision and recall per claim type.

Fails if any claim type with enough examples drops below the targets, and prints a per-type
report plus the exact cases that went wrong, so regressions are easy to diagnose.
"""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import yaml

from second_source.packs.coding.extract import TextSource, extract_claims

GOLDEN = Path(__file__).parent / "golden" / "claims.yaml"
PRECISION_TARGET = 0.95
RECALL_TARGET = 0.90
MIN_SUPPORT = 3  # claim types with fewer labelled examples are reported but not gated


def _load_cases():
    return yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))["cases"]


def _predict(text):
    source = TextSource(origin="body", text=text, written_at=datetime(2026, 1, 1, tzinfo=UTC))
    return Counter(claim.type for claim in extract_claims("golden", [source]))


def test_golden_set_meets_precision_and_recall_targets():
    true_positive, false_positive, false_negative = Counter(), Counter(), Counter()
    mistakes = []

    # Compare predicted and expected claim types per case, as multisets
    # (a case can legitimately contain two FIXES_ISSUE claims).
    for case in _load_cases():
        expected = Counter(case["expected"])
        predicted = _predict(case["text"])
        for claim_type in expected | predicted:
            hits = min(expected[claim_type], predicted[claim_type])
            true_positive[claim_type] += hits
            false_positive[claim_type] += predicted[claim_type] - hits
            false_negative[claim_type] += expected[claim_type] - hits
        if expected != predicted:
            mistakes.append(f"  {case['name']}: expected {dict(expected)}, got {dict(predicted)}")

    report, failures = [], []
    for claim_type in sorted(true_positive | false_positive | false_negative):
        tp, fp, fn = (
            true_positive[claim_type],
            false_positive[claim_type],
            false_negative[claim_type],
        )
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        support = tp + fn
        report.append(
            f"  {claim_type:<13} precision={precision:.2f} recall={recall:.2f} n={support}"
        )
        if support >= MIN_SUPPORT and (precision < PRECISION_TARGET or recall < RECALL_TARGET):
            failures.append(claim_type)

    message = "\n".join(["Per-type results:", *report, "Mismatched cases:", *mistakes])
    assert not failures, f"below target for {failures}\n{message}"


def test_golden_cases_are_well_formed():
    names = [case["name"] for case in _load_cases()]
    assert len(names) == len(set(names)), "case names must be unique"
    for case in _load_cases():
        assert isinstance(case["text"], str) and case["text"].strip()
        assert isinstance(case["expected"], list)
