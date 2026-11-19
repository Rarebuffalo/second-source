"""Expression layer: a thin, safe wrapper around CEL (Common Expression Language).

Checks are written as small CEL expressions in YAML (for example
``"checks.exists(c, c.conclusion == 'failure')"``). This module is the only place that
talks to the CEL library, so the rest of the engine works with plain Python values.

It provides four things:

1. ``to_cel`` / ``from_cel`` convert between Python values and CEL values.
2. ``FunctionRegistry`` holds the pure functions that packs make available to expressions.
3. ``Evaluator`` compiles each expression once (cached) and evaluates it.
4. ``ExpressionError`` is the single error type callers need to handle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

import celpy
from celpy import celtypes
from celpy.celparser import CELParseError
from celpy.evaluation import CELEvalError, CELSyntaxError, CELUnsupportedError

# Errors raised by the CEL library that mean "this expression could not be compiled or
# evaluated". We translate all of them into ExpressionError.
_CEL_ERRORS = (CELParseError, CELEvalError, CELSyntaxError, CELUnsupportedError)


class ExpressionError(Exception):
    """An expression failed to compile or evaluate. Carries the expression text for debugging."""

    def __init__(self, expression: str, message: str) -> None:
        super().__init__(f"{message} (expression: {expression!r})")
        self.expression = expression


# --------------------------------------------------------------------------------------------
# Value conversion
# --------------------------------------------------------------------------------------------


def to_cel(value: Any) -> Any:
    """Convert a plain Python value (recursively) into the matching CEL value.

    Datetimes must be timezone-aware; naive datetimes are rejected because comparing them
    with aware ones would silently give wrong answers.
    """
    # Order matters: bool is a subclass of int in Python, so test it first.
    if value is None:
        return celtypes.NullType()
    if isinstance(value, bool):
        return celtypes.BoolType(value)
    if isinstance(value, Enum):
        return to_cel(value.value)
    if isinstance(value, int):
        return celtypes.IntType(value)
    if isinstance(value, float):
        return celtypes.DoubleType(value)
    if isinstance(value, str):
        return celtypes.StringType(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to an expression; use timezone-aware UTC")
        return celtypes.TimestampType(value.astimezone(UTC))
    if isinstance(value, timedelta):
        # CEL durations take whole seconds plus nanoseconds.
        whole_seconds = int(value.total_seconds())
        nanos = value.microseconds * 1000 if value.total_seconds() >= 0 else 0
        return celtypes.DurationType(seconds=whole_seconds, nanos=nanos)
    if isinstance(value, Mapping):
        # Keys whose value is None are left out. cel-python does not compare null reliably
        # (e.g. `x == null` can be false for a null x), so "no value" is expressed as a missing
        # key and checks test for it with `has(x.field)`.
        return celtypes.MapType(
            {celtypes.StringType(str(k)): to_cel(v) for k, v in value.items() if v is not None}
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value) if isinstance(value, (set, frozenset)) else value
        return celtypes.ListType([to_cel(v) for v in items])
    raise TypeError(f"cannot convert {type(value).__name__} to a CEL value")


def from_cel(value: Any) -> Any:
    """Convert a CEL value (recursively) back into a plain Python value."""
    # Order matters: CEL's BoolType subclasses int, and TimestampType subclasses datetime.
    if isinstance(value, celtypes.NullType):
        return None
    if isinstance(value, celtypes.BoolType):
        return bool(value)
    if isinstance(value, (celtypes.IntType, celtypes.UintType)):
        return int(value)
    if isinstance(value, celtypes.DoubleType):
        return float(value)
    if isinstance(value, celtypes.StringType):
        return str(value)
    if isinstance(value, celtypes.TimestampType):
        return datetime.fromtimestamp(value.timestamp(), tz=UTC)
    if isinstance(value, celtypes.DurationType):
        return timedelta(seconds=value.total_seconds())
    if isinstance(value, celtypes.MapType):
        return {str(k): from_cel(v) for k, v in value.items()}
    if isinstance(value, celtypes.ListType):
        return [from_cel(v) for v in value]
    return value


# --------------------------------------------------------------------------------------------
# Functions available inside expressions
# --------------------------------------------------------------------------------------------

CelFunction = Callable[..., Any]


def _cel_sum(values: Any) -> Any:
    """``sum(list)``: total of a list of numbers. Returns a double if any element is a double."""
    numbers = [from_cel(v) for v in values]
    total = sum(numbers)
    return celtypes.DoubleType(total) if isinstance(total, float) else celtypes.IntType(total)


# Functions every pack can rely on. Packs add their own through FunctionRegistry.register.
_BUILTINS: dict[str, CelFunction] = {"sum": _cel_sum}


class FunctionRegistry:
    """Named, pure functions that expressions may call.

    Packs register helpers such as ``is_test_check(name)``. Functions must be deterministic
    and must not perform I/O, so that every verdict can be replayed.
    """

    def __init__(self) -> None:
        self._functions: dict[str, CelFunction] = dict(_BUILTINS)

    def register(self, name: str, function: CelFunction) -> None:
        """Add a function. Re-using an existing name is an error, so packs cannot clash."""
        if name in self._functions:
            raise ValueError(f"expression function {name!r} is already registered")
        self._functions[name] = function

    def as_dict(self) -> dict[str, CelFunction]:
        return dict(self._functions)

    def __contains__(self, name: str) -> bool:
        return name in self._functions


# --------------------------------------------------------------------------------------------
# Compilation and evaluation
# --------------------------------------------------------------------------------------------


class Evaluator:
    """Compiles expressions once and evaluates them against an activation (variables).

    One Evaluator is created per Engine. The compile cache is keyed by expression text,
    so a check evaluated thousands of times is only parsed once.
    """

    def __init__(self, functions: FunctionRegistry | None = None) -> None:
        self._env = celpy.Environment()
        self._functions = (functions or FunctionRegistry()).as_dict()
        self._programs: dict[str, Any] = {}

    def compile(self, expression: str) -> None:
        """Compile and cache an expression. Raises ExpressionError on syntax errors.

        Called when checks are loaded, so broken expressions fail early, not mid-run.
        """
        self._program(expression)

    def evaluate(self, expression: str, activation: Mapping[str, Any]) -> Any:
        """Evaluate a compiled expression. ``activation`` must already hold CEL values.

        Returns a CEL value; use ``from_cel`` to turn it into plain Python.
        """
        program = self._program(expression)
        try:
            return program.evaluate(dict(activation))
        except _CEL_ERRORS as error:
            raise ExpressionError(expression, f"evaluation failed: {error}") from error
        except (TypeError, ValueError, KeyError, ZeroDivisionError) as error:
            # Errors raised inside registered functions surface here.
            raise ExpressionError(expression, f"evaluation failed: {error}") from error

    def evaluate_bool(self, expression: str, activation: Mapping[str, Any]) -> bool:
        """Evaluate an expression that must produce a boolean (used for conditions)."""
        result = self.evaluate(expression, activation)
        if not isinstance(result, celtypes.BoolType):
            raise ExpressionError(expression, f"expected a boolean, got {type(result).__name__}")
        return bool(result)

    def _program(self, expression: str) -> Any:
        """Return the cached compiled program for an expression, compiling it if needed."""
        program = self._programs.get(expression)
        if program is None:
            try:
                ast = self._env.compile(expression)
                program = self._env.program(ast, functions=self._functions)
            except _CEL_ERRORS as error:
                raise ExpressionError(expression, f"invalid expression: {error}") from error
            self._programs[expression] = program
        return program
