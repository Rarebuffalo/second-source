"""Tests for check definitions: validation, parameters and evaluation (core/checks.py).

The example check is deliberately domain-neutral: "a promised payment arrived in full".
"""

import copy
from datetime import UTC, datetime, timedelta

import pytest

from second_source.core.checks import (
    ENGINE_ERROR,
    CheckDefinition,
    CheckDefinitionError,
    CompiledChecks,
    ParamSpec,
    evaluate_check,
)
from second_source.core.expr import Evaluator
from second_source.core.model import CheckKind, Claim, Episode, Evidence, Level, Result

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
EPISODE = Episode(id="ep-1", kind="task", agent="agent-x", opened_at=NOW - timedelta(days=3))

BASE = {
    "id": "demo.paid_in_full",
    "version": "1.0.0",
    "kind": "state",
    "level": "L4",
    "applies_to": ["PAYMENT_PROMISED"],
    "params": {"tolerance": {"type": "int", "default": 0, "min": 0, "max": 100}},
    "evidence": {
        "payments": {
            "record_type": "payment",
            "where": "e.account == claim.attributes.account && e.status == 'SUCCESS'",
        }
    },
    "derive": {
        "paid": "sum(payments.map(p, p.amount))",
        "short_by": "claim.attributes.amount - paid",
    },
    "outcomes": [
        {"when": "'payment' in stale", "result": "UNVERIFIABLE", "reason": "SOURCE_STALE"},
        {"when": "short_by <= params.tolerance", "result": "VERIFIED", "reason": "PAID_IN_FULL"},
        {"when": "paid > 0", "result": "CONTRADICTED", "reason": "PARTIAL_PAYMENT"},
        {"otherwise": True, "result": "CONTRADICTED", "reason": "NO_PAYMENT"},
    ],
    "explain": "Promised {short_by} short; received {paid}",
}


def definition(**changes):
    data = copy.deepcopy(BASE)
    data.update(changes)
    return CheckDefinition.from_dict(data)


def claim(amount=500, account="acc-1"):
    return Claim(
        id="c-1",
        episode_id="ep-1",
        type="PAYMENT_PROMISED",
        made_at=NOW - timedelta(days=2),
        attributes={"amount": amount, "account": account},
    )


def payment(ref, amount, *, account="acc-1", status="SUCCESS", version=1):
    return Evidence(
        record_type="payment",
        external_ref=ref,
        observed_at=NOW - timedelta(hours=1),
        version=version,
        attributes={"amount": amount, "account": account, "status": status},
    )


def run(evidence, *, check=None, the_claim=None, **kwargs):
    return evaluate_check(
        check or definition(),
        the_claim or claim(),
        EPISODE,
        evidence,
        now=NOW,
        evaluator=Evaluator(),
        **kwargs,
    )


# ---- validation ------------------------------------------------------------------------------


def test_valid_definition_loads():
    check = definition()
    assert check.kind is CheckKind.STATE
    assert check.level is Level.L4
    assert [name for name, _ in check.derive] == ["paid", "short_by"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"id": "nodot"}, "id must look like"),
        ({"version": "1.0"}, "semantic"),
        ({"kind": "judge"}, "cannot be defined in YAML"),
        ({"kind": "magic"}, "not a valid CheckKind"),
        ({"level": "L9"}, "not a valid Level"),
        ({"applies_to": []}, "applies_to"),
        ({"outcomes": []}, "must not be empty"),
        (
            {"outcomes": [{"otherwise": True, "result": "VERIFIED", "reason": "OK"}] * 2},
            "exactly one 'otherwise'",
        ),
        (
            {"outcomes": [{"when": "true", "result": "VERIFIED", "reason": "OK"}]},
            "exactly one 'otherwise'",
        ),
        ({"outcomes": [{"otherwise": True, "result": "MAYBE", "reason": "OK"}]}, "Result"),
        ({"outcomes": [{"otherwise": True, "result": "VERIFIED", "reason": "ok"}]}, "UPPER_SNAKE"),
        ({"derive": {"claim": "1"}}, "reserved"),
        ({"derive": {"payments": "1"}}, "duplicated"),
        ({"params": {"t": {"type": "int", "default": 500, "max": 100}}}, "above max"),
        ({"params": {"t": {"type": "decimal", "default": 1}}}, "needs a type"),
        ({"evidence": {"x": {}}}, "needs a record_type"),
    ],
)
def test_invalid_definitions_are_rejected(changes, message):
    with pytest.raises(CheckDefinitionError, match=message):
        definition(**changes)


def test_param_resolution_and_bounds():
    spec = ParamSpec("days", "int", 2, min=0, max=10)
    assert spec.resolve() == 2
    assert spec.resolve(5) == 5
    with pytest.raises(CheckDefinitionError):
        spec.resolve(11)
    with pytest.raises(CheckDefinitionError):
        spec.resolve(True)  # booleans are not integers here
    assert ParamSpec("ratio", "double", 1).resolve() == 1.0  # ints are accepted as doubles


def test_compiled_checks_reject_bad_expressions_early():
    broken = definition(derive={"paid": "sum(payments.map(p, p.amount)"})  # missing ')'
    with pytest.raises(CheckDefinitionError, match="invalid expression"):
        CompiledChecks.build([broken], Evaluator())


def test_compiled_checks_reject_duplicate_ids():
    with pytest.raises(CheckDefinitionError, match="more than once"):
        CompiledChecks.build([definition(), definition()], Evaluator())


def test_compiled_checks_index_by_claim_type():
    compiled = CompiledChecks.build([definition()], Evaluator())
    assert [c.id for c in compiled.for_claim_type("PAYMENT_PROMISED")] == ["demo.paid_in_full"]
    assert compiled.for_claim_type("OTHER") == []


# ---- evaluation ------------------------------------------------------------------------------


def test_full_payment_is_verified_with_explanation():
    result = run([payment("p1", 300), payment("p2", 200)])
    assert result.result is Result.VERIFIED
    assert result.reason == "PAID_IN_FULL"
    assert result.derived == {"paid": 500, "short_by": 0}
    assert result.explanation == "Promised 0 short; received 500"


def test_partial_and_missing_payments_are_contradicted():
    assert run([payment("p1", 100)]).reason == "PARTIAL_PAYMENT"
    assert run([]).reason == "NO_PAYMENT"


def test_where_filter_excludes_other_accounts_and_failed_payments():
    evidence = [payment("p1", 500, account="acc-2"), payment("p2", 500, status="FAILED")]
    assert run(evidence).reason == "NO_PAYMENT"


def test_reobserved_evidence_is_not_double_counted():
    # The same payment observed twice must count once.
    result = run([payment("p1", 300), payment("p1", 300)])
    assert result.derived["paid"] == 300


def test_stale_source_gives_unverifiable_not_contradicted():
    result = run([], stale=frozenset({"payment"}))
    assert result.result is Result.UNVERIFIABLE
    assert result.reason == "SOURCE_STALE"


def test_param_override_changes_the_outcome():
    assert run([payment("p1", 450)], params={"tolerance": 50}).result is Result.VERIFIED


def test_invalid_param_override_is_an_engine_error():
    result = run([payment("p1", 500)], params={"tolerance": 1000})
    assert result.result is Result.UNVERIFIABLE
    assert result.reason == ENGINE_ERROR


def test_expression_failure_is_an_engine_error_not_a_contradiction():
    # The claim lacks the 'amount' attribute, so 'short_by' cannot be computed.
    broken_claim = Claim(
        id="c-2",
        episode_id="ep-1",
        type="PAYMENT_PROMISED",
        made_at=NOW,
        attributes={"account": "acc-1"},
    )
    result = run([payment("p1", 500)], the_claim=broken_claim)
    assert result.result is Result.UNVERIFIABLE
    assert result.reason == ENGINE_ERROR
    assert "amount" in result.explanation


# ---- replayability ---------------------------------------------------------------------------


def test_same_inputs_give_same_hash_and_result():
    evidence = [payment("p1", 500)]
    first, second = run(evidence), run(list(reversed(evidence)))
    assert first.inputs_hash == second.inputs_hash
    assert first.result == second.result


def test_hash_changes_when_inputs_change():
    base = run([payment("p1", 500)]).inputs_hash
    assert run([payment("p1", 499)]).inputs_hash != base  # evidence changed
    assert run([payment("p1", 500)], params={"tolerance": 1}).inputs_hash != base
    assert run([payment("p1", 500)], stale=frozenset({"payment"})).inputs_hash != base


def test_irrelevant_evidence_does_not_change_the_hash():
    unrelated = Evidence(record_type="ticket", external_ref="t1", observed_at=NOW)
    assert run([payment("p1", 500)]).inputs_hash == run([payment("p1", 500), unrelated]).inputs_hash


def test_evidence_attributes_cannot_overwrite_standard_fields():
    sneaky = Evidence(
        record_type="payment",
        external_ref="p1",
        observed_at=NOW,
        attributes={"record_type": "fake", "amount": 500, "account": "acc-1", "status": "SUCCESS"},
    )
    assert run([sneaky]).result is Result.VERIFIED  # still selected as a real "payment"


# ---- guards ----------------------------------------------------------------------------------

GUARD_OUTCOMES = [
    {"when": "paid > 0", "result": "UNVERIFIABLE", "reason": "LIMIT_CHANGED"},
    {"otherwise": True, "result": "VERIFIED", "reason": "NOTHING_FOUND"},
]


def test_a_guard_may_only_withdraw_a_verification():
    # The cardinal rule as a schema rule: a guard cannot accuse or keep a claim pending.
    with pytest.raises(CheckDefinitionError, match="only be VERIFIED or UNVERIFIABLE"):
        definition(role="guard")  # BASE has CONTRADICTED outcomes
    guard = definition(id="demo.guard", role="guard", outcomes=GUARD_OUTCOMES)
    assert guard.role == "guard"
    with pytest.raises(CheckDefinitionError, match="role must be"):
        definition(role="judge")


def test_a_guard_cannot_be_the_only_check_for_a_claim_type():
    guard = definition(id="demo.guard", role="guard", outcomes=GUARD_OUTCOMES)
    with pytest.raises(CheckDefinitionError, match="only guards"):
        CompiledChecks.build([guard], Evaluator())
    assert CompiledChecks.build([definition(), guard], Evaluator())


def test_guard_runs_carry_their_role():
    guard = definition(id="demo.guard", role="guard", outcomes=GUARD_OUTCOMES)
    payment = Evidence("payment", "p1", NOW, {"account": "acc-1", "status": "SUCCESS", "amount": 5})
    run = evaluate_check(guard, claim(), EPISODE, [payment], now=NOW, evaluator=Evaluator())
    assert (run.role, run.reason) == ("guard", "LIMIT_CHANGED")
