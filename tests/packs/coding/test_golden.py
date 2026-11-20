"""Golden-set gate for claim extraction: precision and recall per claim type.

Fails if any claim type with enough examples drops below the targets, and prints a per-type
report plus the exact cases that went wrong, so regressions are easy to diagnose.
"""

from pathlib import Path

import yaml

from second_source.benchmark.golden import GoldenCase, score

GOLDEN = Path(__file__).parent / "golden" / "claims.yaml"
PRECISION_TARGET = 0.95
RECALL_TARGET = 0.90
MIN_SUPPORT = 3  # claim types with fewer labelled examples are reported but not gated


def _load_cases():
    return yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))["cases"]


def test_golden_set_meets_precision_and_recall_targets():
    result = score(
        GoldenCase(name=case["name"], text=case["text"], expected=tuple(case["expected"]))
        for case in _load_cases()
    )
    failures = [
        claim_type
        for claim_type, metrics in result.per_type.items()
        if metrics.support >= MIN_SUPPORT
        and (metrics.precision < PRECISION_TARGET or metrics.recall < RECALL_TARGET)
    ]
    assert not failures, f"below target for {failures}\n{result.report()}"


def test_golden_cases_are_well_formed():
    names = [case["name"] for case in _load_cases()]
    assert len(names) == len(set(names)), "case names must be unique"
    for case in _load_cases():
        assert isinstance(case["text"], str) and case["text"].strip()
        assert isinstance(case["expected"], list)
