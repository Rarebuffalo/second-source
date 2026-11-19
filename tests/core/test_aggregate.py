"""Tests for verdict aggregation (core/aggregate.py)."""

import pytest

from second_source.core.aggregate import NO_APPLICABLE_CHECKS, aggregate
from second_source.core.model import CheckKind, CheckRun, Level, Result


def run(check_id, result, reason="R", kind=CheckKind.STATE):
    return CheckRun(
        check_id=check_id,
        check_version="1.0.0",
        kind=kind,
        level=Level.L4,
        claim_id="c1",
        result=result,
        reason=reason,
        inputs_hash="h",
    )


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([Result.VERIFIED, Result.VERIFIED], Result.VERIFIED),
        ([Result.VERIFIED, Result.CONTRADICTED], Result.CONTRADICTED),
        ([Result.PENDING, Result.CONTRADICTED], Result.CONTRADICTED),
        ([Result.VERIFIED, Result.PENDING], Result.PENDING),
        ([Result.UNVERIFIABLE, Result.PENDING], Result.PENDING),
        ([Result.VERIFIED, Result.UNVERIFIABLE], Result.UNVERIFIABLE),
    ],
)
def test_precedence(results, expected):
    runs = [run(f"x.c{i}", r) for i, r in enumerate(results)]
    assert aggregate("c1", runs).result is expected


def test_no_runs_is_unverifiable():
    verdict = aggregate("c1", [])
    assert verdict.result is Result.UNVERIFIABLE
    assert verdict.reasons == (NO_APPLICABLE_CHECKS,)


def test_judge_runs_never_decide():
    # A model-judged contradiction alone must not contradict the claim.
    runs = [
        run("x.state", Result.VERIFIED),
        run("x.judge", Result.CONTRADICTED, kind=CheckKind.JUDGE),
    ]
    verdict = aggregate("c1", runs)
    assert verdict.result is Result.VERIFIED
    assert len(verdict.check_runs) == 2  # still recorded for transparency


def test_only_judge_runs_means_no_applicable_checks():
    verdict = aggregate("c1", [run("x.judge", Result.VERIFIED, kind=CheckKind.JUDGE)])
    assert verdict.reasons == (NO_APPLICABLE_CHECKS,)


def test_optional_checks_do_not_decide():
    runs = [run("x.required", Result.VERIFIED), run("x.optional", Result.CONTRADICTED)]
    verdict = aggregate("c1", runs, required=frozenset({"x.required"}))
    assert verdict.result is Result.VERIFIED


def test_reasons_come_from_the_deciding_runs_in_stable_order():
    runs = [
        run("x.b", Result.CONTRADICTED, "B_FAILED"),
        run("x.a", Result.CONTRADICTED, "A_FAILED"),
        run("x.c", Result.VERIFIED, "C_OK"),
    ]
    assert aggregate("c1", runs).reasons == ("A_FAILED", "B_FAILED")
