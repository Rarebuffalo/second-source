"""Tests for verdict aggregation (core/aggregate.py)."""

import itertools
from dataclasses import replace

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


# ---- guards ----------------------------------------------------------------------------------


def guard(check_id, result, reason="G"):
    return replace(run(check_id, result, reason), role="guard")


def test_a_required_guard_withdraws_a_verification():
    verdict = aggregate(
        "c1",
        [run("a.ci", Result.VERIFIED, "PASSED"), guard("a.guard", Result.UNVERIFIABLE, "SKIPS")],
    )
    assert (verdict.result, verdict.reasons) == (Result.UNVERIFIABLE, ("SKIPS",))


def test_a_guard_that_found_nothing_adds_no_reason():
    verdict = aggregate(
        "c1", [run("a.ci", Result.VERIFIED, "PASSED"), guard("a.guard", Result.VERIFIED, "OK")]
    )
    assert (verdict.result, verdict.reasons) == (Result.VERIFIED, ("PASSED",))


@pytest.mark.parametrize("deciding", [Result.CONTRADICTED, Result.PENDING])
def test_a_guard_never_overrides_a_contradiction_or_pending(deciding):
    verdict = aggregate("c1", [run("a.ci", deciding), guard("a.guard", Result.UNVERIFIABLE)])
    assert verdict.result is deciding


def test_optional_guards_become_caveats_without_changing_the_result():
    runs = [
        run("a.ci", Result.VERIFIED, "PASSED"),
        guard("a.note", Result.UNVERIFIABLE, "CI_CHANGED"),
        guard("a.quiet", Result.VERIFIED, "NOTHING"),
    ]
    verdict = aggregate("c1", runs, required=frozenset({"a.ci"}))
    assert (verdict.result, verdict.caveats) == (Result.VERIFIED, ("CI_CHANGED",))


def test_guards_can_only_move_verified_to_unverifiable():
    # Property: for every combination of deciding results, adding a guard of any outcome moves
    # the verdict nowhere except VERIFIED -> UNVERIFIABLE.
    results = list(Result)
    for combo in itertools.product(results, repeat=2):
        base = [run(f"a.c{i}", result) for i, result in enumerate(combo)]
        before = aggregate("c1", base).result
        for guard_result in (Result.VERIFIED, Result.UNVERIFIABLE):
            after = aggregate("c1", [*base, guard("a.guard", guard_result)]).result
            assert after == before or (before, after) == (Result.VERIFIED, Result.UNVERIFIABLE)
