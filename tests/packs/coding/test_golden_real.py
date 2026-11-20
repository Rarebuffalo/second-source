"""Gate for claim extraction on the real golden set (real agent PR bodies from AIDev).

- **dev split:** gated. Precision must stay at or above PRECISION_FLOOR for every claim type
  with enough examples (a false claim can lead to a false accusation). Recall must stay at or
  above the per-type floor below, which only ever moves up as the rules improve.
- **test split:** never used for tuning. Scored only when SECOND_SOURCE_SCORE_TEST_SPLIT=1, to
  produce the numbers we publish against the 0.95 precision target
  (docs/benchmark.md, section 3).
"""

import os
from pathlib import Path

import pytest

from second_source.benchmark.golden import DEV, TEST, in_split, load_cases, score

CASES = Path(__file__).parent / "golden" / "real" / "cases.jsonl"
PRECISION_TARGET = 0.95
# With 212 dev cases there are about 40 predictions per claim type, so the dev split is gated
# at the full target (one false positive moves precision by ~2.5 points, not ~8 as on the
# first, 62-case dev split).
PRECISION_FLOOR = PRECISION_TARGET
MIN_SUPPORT = 3  # claim types with fewer labelled examples are reported but not gated

# Recall floors on the dev split. Raise a floor when a rule change improves recall; never lower
# one without a written reason in the commit message. (Reset once, when the set grew from 120
# to 402 PRs: the old floors were measured on 62 dev cases and did not hold on 212.)
RECALL_FLOOR = {
    "TESTS_PASS": 0.9,
    "BUILD_OK": 0.5,
    "LINT_OK": 0.85,
    "TESTS_ADDED": 0.75,
    "FIXES_ISSUE": 0.95,
    "DOCS_UPDATED": 0.6,
}


def _gate(split, precision_floor=PRECISION_FLOOR):
    result = score(in_split(load_cases(CASES), split))
    failures = []
    for claim_type, metrics in result.per_type.items():
        if metrics.support < MIN_SUPPORT:
            continue
        if metrics.precision < precision_floor:
            failures.append(f"{claim_type} precision {metrics.precision:.2f}")
        if metrics.recall < RECALL_FLOOR.get(claim_type, 0.0):
            failures.append(f"{claim_type} recall {metrics.recall:.2f}")
    return failures, result.report()


def test_dev_split_meets_precision_target_and_recall_floors():
    failures, report = _gate(DEV)
    assert not failures, f"{failures}\n{report}"


@pytest.mark.skipif(
    os.environ.get("SECOND_SOURCE_SCORE_TEST_SPLIT") != "1",
    reason="the test split is scored only on purpose, never while tuning rules",
)
def test_test_split_report():
    failures, report = _gate(TEST, precision_floor=PRECISION_TARGET)
    print(report)
    assert not failures, f"{failures}\n{report}"


def test_cases_are_well_formed():
    cases = load_cases(CASES)
    assert len(cases) == 402
    assert len({case.name for case in cases}) == len(cases), "URLs must be unique"
    assert {case.agent for case in cases} == {
        "claude_code",
        "codex",
        "copilot",
        "cursor",
        "devin",
        "jules",
    }
