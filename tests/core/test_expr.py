"""Tests for the CEL expression layer (core/expr.py)."""

from datetime import UTC, datetime, timedelta

import pytest

from second_source.core.expr import (
    Evaluator,
    ExpressionError,
    FunctionRegistry,
    from_cel,
    to_cel,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


# ---- value conversion ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        42,
        -7,
        1.5,
        "text",
        NOW,
        timedelta(hours=2),
        {"a": 1, "nested": {"b": [1, 2]}},
        [1, "two", None, False],
    ],
)
def test_round_trip_preserves_value(value):
    assert from_cel(to_cel(value)) == value


def test_bool_is_not_converted_to_int():
    # bool subclasses int in Python; the converter must keep it a boolean.
    assert from_cel(to_cel(True)) is True


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="naive datetime"):
        to_cel(datetime(2026, 1, 1))


def test_sets_are_converted_in_sorted_order():
    assert from_cel(to_cel({"b", "a"})) == ["a", "b"]


def test_unsupported_type_raises():
    with pytest.raises(TypeError):
        to_cel(object())


# ---- evaluation ------------------------------------------------------------------------------


def make_activation(**values):
    return {name: to_cel(value) for name, value in values.items()}


def test_evaluates_list_macros_and_maps():
    evaluator = Evaluator()
    items = [{"ok": True, "amount": 5}, {"ok": False, "amount": 3}, {"ok": True, "amount": 2}]
    result = evaluator.evaluate(
        "items.filter(i, i.ok).map(i, i.amount)", make_activation(items=items)
    )
    assert from_cel(result) == [5, 2]


def test_builtin_sum_handles_ints_and_doubles():
    evaluator = Evaluator()
    assert from_cel(evaluator.evaluate("sum(xs)", make_activation(xs=[1, 2, 3]))) == 6
    assert from_cel(evaluator.evaluate("sum(xs)", make_activation(xs=[1, 2.5]))) == 3.5
    assert from_cel(evaluator.evaluate("sum(xs)", make_activation(xs=[]))) == 0


def test_timestamps_and_durations_compare_correctly():
    evaluator = Evaluator()
    activation = make_activation(now=NOW, then=NOW - timedelta(hours=2))
    assert evaluator.evaluate_bool("now - then > duration('1h')", activation)
    assert not evaluator.evaluate_bool("then + duration('3h') < now", activation)


def test_evaluate_bool_rejects_non_boolean_results():
    with pytest.raises(ExpressionError, match="expected a boolean"):
        Evaluator().evaluate_bool("1 + 1", {})


def test_syntax_error_is_reported_at_compile_time():
    with pytest.raises(ExpressionError, match="invalid expression"):
        Evaluator().compile("1 +")


def test_missing_field_is_an_expression_error():
    with pytest.raises(ExpressionError, match="evaluation failed"):
        Evaluator().evaluate("m.missing == 1", make_activation(m={"present": 1}))


def test_compiled_programs_are_cached():
    evaluator = Evaluator()
    evaluator.compile("1 == 1")
    first = evaluator._program("1 == 1")
    assert evaluator._program("1 == 1") is first


# ---- function registry -----------------------------------------------------------------------


def test_registered_function_is_callable_from_expressions():
    registry = FunctionRegistry()
    registry.register("is_even", lambda n: to_cel(int(n) % 2 == 0))
    evaluator = Evaluator(registry)
    assert evaluator.evaluate_bool("is_even(4)", {})
    assert not evaluator.evaluate_bool("is_even(3)", {})


def test_duplicate_function_names_are_rejected():
    registry = FunctionRegistry()
    with pytest.raises(ValueError, match="already registered"):
        registry.register("sum", lambda xs: xs)


def test_errors_inside_functions_become_expression_errors():
    registry = FunctionRegistry()

    def explode(_value):
        raise ValueError("boom")

    registry.register("explode", explode)
    with pytest.raises(ExpressionError, match="boom"):
        Evaluator(registry).evaluate("explode(1)", {})
