"""Tests for the engine entry point (core/engine.py)."""

from datetime import UTC, datetime

import pytest

from second_source.core.checks import CheckDefinition, CheckDefinitionError
from second_source.core.engine import Engine
from second_source.core.expr import FunctionRegistry, to_cel
from second_source.core.model import Claim, Episode, Evidence, Result

NOW = datetime(2026, 11, 20, tzinfo=UTC)
EPISODE = Episode(id="ep", kind="task", agent="a", opened_at=NOW)


def check(check_id, applies_to, record_type, outcome_when, *, required=True, params=None):
    """A minimal check: VERIFIED when `outcome_when` holds, otherwise CONTRADICTED."""
    return CheckDefinition.from_dict(
        {
            "id": check_id,
            "version": "1.0.0",
            "kind": "state",
            "level": "L4",
            "applies_to": applies_to,
            "required": required,
            "params": params or {},
            "evidence": {"items": {"record_type": record_type}},
            "outcomes": [
                {"when": outcome_when, "result": "VERIFIED", "reason": "OK"},
                {"otherwise": True, "result": "CONTRADICTED", "reason": "NOT_OK"},
            ],
        }
    )


def claim(claim_id, claim_type, episode_id="ep"):
    return Claim(id=claim_id, episode_id=episode_id, type=claim_type, made_at=NOW)


def item(ref, record_type="thing"):
    return Evidence(record_type=record_type, external_ref=ref, observed_at=NOW)


def test_one_verdict_per_claim_in_input_order():
    engine = Engine([check("t.has_items", ["A", "B"], "thing", "size(items) > 0")])
    verdicts = engine.verify(EPISODE, [claim("c2", "B"), claim("c1", "A")], [item("x")], now=NOW)
    assert [v.claim_id for v in verdicts] == ["c2", "c1"]
    assert all(v.result is Result.VERIFIED for v in verdicts)


def test_claim_types_without_checks_are_unverifiable():
    engine = Engine([check("t.has_items", ["A"], "thing", "true")])
    (verdict,) = engine.verify(EPISODE, [claim("c1", "UNKNOWN")], [], now=NOW)
    assert verdict.result is Result.UNVERIFIABLE


def test_optional_checks_are_recorded_but_do_not_decide():
    engine = Engine(
        [
            check("t.required", ["A"], "thing", "true"),
            check("t.optional", ["A"], "thing", "false", required=False),
        ]
    )
    (verdict,) = engine.verify(EPISODE, [claim("c1", "A")], [], now=NOW)
    assert verdict.result is Result.VERIFIED
    assert len(verdict.check_runs) == 2


def test_param_overrides_are_applied_per_check():
    params = {"minimum": {"type": "int", "default": 2}}
    engine = Engine(
        [check("t.enough", ["A"], "thing", "size(items) >= params.minimum", params=params)]
    )
    evidence = [item("x")]
    (default,) = engine.verify(EPISODE, [claim("c1", "A")], evidence, now=NOW)
    (relaxed,) = engine.verify(
        EPISODE, [claim("c1", "A")], evidence, now=NOW, params={"t.enough": {"minimum": 1}}
    )
    assert default.result is Result.CONTRADICTED
    assert relaxed.result is Result.VERIFIED


def test_evidence_types_for_claims():
    engine = Engine(
        [check("t.one", ["A"], "payments", "true"), check("t.two", ["B"], "tickets", "true")]
    )
    assert engine.evidence_types_for([claim("c1", "A")]) == {"payments"}
    assert engine.evidence_types_for([claim("c1", "A"), claim("c2", "B")]) == {
        "payments",
        "tickets",
    }


def test_pack_functions_are_available_to_checks():
    functions = FunctionRegistry()
    functions.register("is_big", lambda n: to_cel(int(n) > 100))
    engine = Engine([check("t.big", ["A"], "thing", "is_big(500)")], functions)
    (verdict,) = engine.verify(EPISODE, [claim("c1", "A")], [], now=NOW)
    assert verdict.result is Result.VERIFIED


def test_broken_expressions_fail_when_the_engine_is_built():
    with pytest.raises(CheckDefinitionError):
        Engine([check("t.broken", ["A"], "thing", "size(items >")])


def test_rejects_naive_now_and_foreign_claims():
    engine = Engine([check("t.any", ["A"], "thing", "true")])
    with pytest.raises(ValueError, match="timezone-aware"):
        engine.verify(EPISODE, [], [], now=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="does not belong"):
        engine.verify(EPISODE, [claim("c1", "A", episode_id="other")], [], now=NOW)
