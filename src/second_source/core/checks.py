"""Check definitions: how a claim type is verified.

A check is declarative data (normally written in YAML) made of:

- ``params``: tunable values with defaults and bounds (e.g. a time window);
- ``evidence``: named sets of evidence records, selected by type and an optional filter;
- ``derive``: named intermediate values, computed in order;
- ``outcomes``: ordered conditions; the first one that is true decides the result.

This module validates definitions when they are loaded (so mistakes fail early) and
evaluates them into ``CheckRun`` objects. Evaluation is pure: no I/O, no clock. The current
time and all evidence are passed in, so the same inputs always give the same result.
"""

from __future__ import annotations

import re
import string
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from celpy import celtypes

from second_source.core.evidence import dedupe_evidence
from second_source.core.expr import Evaluator, ExpressionError, from_cel, to_cel
from second_source.core.hashing import sha256_hex
from second_source.core.model import (
    CheckKind,
    CheckRun,
    Claim,
    Episode,
    Evidence,
    Level,
    Result,
)

# Names the engine itself provides to expressions; evidence sets and derived values may not
# reuse them.
ROLES = frozenset({"check", "guard"})
RESERVED_NAMES = frozenset({"claim", "episode", "params", "now", "stale", "e"})

# Kinds that can be expressed as YAML checks in v1. Judge checks (model-assessed) and human
# checks (review decisions) are handled elsewhere and are not loaded from definitions.
SUPPORTED_KINDS = frozenset({CheckKind.RULE, CheckKind.STATE, CheckKind.TEMPORAL})

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_CHECK_ID = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")  # e.g. "coding.tests_pass"
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_REASON = re.compile(r"^[A-Z][A-Z0-9_]*$")  # e.g. "TEST_CHECK_FAILED"
_PARAM_TYPES = {"int": int, "double": float, "string": str, "bool": bool}

# Reason used whenever a check cannot be evaluated because of a definition or data problem.
ENGINE_ERROR = "ENGINE_ERROR"


class CheckDefinitionError(ValueError):
    """A check definition is malformed. The message names the check and the problem."""


# --------------------------------------------------------------------------------------------
# Definition model
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    """A tunable parameter with a type, a default and optional bounds."""

    name: str
    type: str
    default: Any
    min: float | None = None
    max: float | None = None

    def resolve(self, value: Any = None) -> Any:
        """Return the effective value (the override if given, else the default), validated."""
        chosen = self.default if value is None else value
        expected = _PARAM_TYPES[self.type]
        # bool is a subclass of int: do not let True pass as an int (or vice versa).
        is_bool_mismatch = (expected is bool) != isinstance(chosen, bool)
        if expected is float and isinstance(chosen, int) and not isinstance(chosen, bool):
            chosen = float(chosen)
        if not isinstance(chosen, expected) or is_bool_mismatch:
            raise CheckDefinitionError(
                f"param {self.name!r} must be {self.type}, got {type(chosen).__name__}"
            )
        if self.min is not None and chosen < self.min:
            raise CheckDefinitionError(f"param {self.name!r}={chosen} is below min {self.min}")
        if self.max is not None and chosen > self.max:
            raise CheckDefinitionError(f"param {self.name!r}={chosen} is above max {self.max}")
        return chosen


@dataclass(frozen=True)
class EvidenceSelector:
    """A named set of evidence: all records of one type, optionally filtered by ``where``.

    Inside ``where`` the current record is available as ``e``.
    """

    name: str
    record_type: str
    where: str | None = None


@dataclass(frozen=True)
class Outcome:
    """One branch of the decision: if ``when`` is true, return ``result`` with ``reason``.

    ``when`` is None for the final ``otherwise`` branch.
    """

    when: str | None
    result: Result
    reason: str


@dataclass(frozen=True)
class CheckDefinition:
    id: str
    version: str
    kind: CheckKind
    level: Level
    applies_to: tuple[str, ...]
    outcomes: tuple[Outcome, ...]
    params: tuple[ParamSpec, ...] = ()
    evidence: tuple[EvidenceSelector, ...] = ()
    derive: tuple[tuple[str, str], ...] = ()  # ordered (name, expression) pairs
    explain: str | None = None
    required: bool = True
    # "guard": a check on top of the deciding checks that can only withdraw a VERIFIED (its
    # outcomes are limited to VERIFIED = "nothing found" and UNVERIFIABLE). It enforces the
    # cardinal rule in the schema: a guard can never accuse. See docs/ARCHITECTURE.md.
    role: str = "check"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CheckDefinition:
        """Build and validate a definition from parsed YAML/JSON data."""
        return _parse_definition(data)

    def expressions(self) -> Iterator[str]:
        """Every expression in this definition (used to pre-compile and fail early)."""
        for selector in self.evidence:
            if selector.where:
                yield selector.where
        for _, expression in self.derive:
            yield expression
        for outcome in self.outcomes:
            if outcome.when:
                yield outcome.when


# --------------------------------------------------------------------------------------------
# Parsing and validation
# --------------------------------------------------------------------------------------------


def _parse_definition(data: Mapping[str, Any]) -> CheckDefinition:
    check_id = str(data.get("id", "")).strip()

    def fail(problem: str) -> CheckDefinitionError:
        return CheckDefinitionError(f"check {check_id or '<missing id>'}: {problem}")

    # Identity and classification.
    if not _CHECK_ID.match(check_id):
        raise fail("id must look like 'pack.check_name'")
    version = str(data.get("version", ""))
    if not _VERSION.match(version):
        raise fail("version must be semantic, e.g. '1.0.0'")
    try:
        kind = CheckKind(data.get("kind"))
        level = Level(data.get("level"))
    except ValueError as error:
        raise fail(str(error)) from error
    if kind not in SUPPORTED_KINDS:
        raise fail(f"kind {kind.value!r} cannot be defined in YAML in this version")

    # Which claim types this check verifies.
    applies_to = data.get("applies_to")
    if not applies_to or not all(isinstance(t, str) and t for t in applies_to):
        raise fail("applies_to must be a non-empty list of claim types")

    # Parameters.
    params = []
    for name, spec in (data.get("params") or {}).items():
        if not _IDENTIFIER.match(name):
            raise fail(f"invalid param name {name!r}")
        if not isinstance(spec, Mapping) or spec.get("type") not in _PARAM_TYPES:
            raise fail(f"param {name!r} needs a type in {sorted(_PARAM_TYPES)}")
        param = ParamSpec(name, spec["type"], spec.get("default"), spec.get("min"), spec.get("max"))
        try:
            param.resolve()  # the default itself must satisfy the type and bounds
        except CheckDefinitionError as error:
            raise fail(str(error)) from error
        params.append(param)

    # Names introduced by evidence sets and derived values must be unique and not reserved.
    seen_names: set[str] = set()

    def claim_name(name: str) -> None:
        if not _IDENTIFIER.match(name) or name in RESERVED_NAMES or name in seen_names:
            raise fail(f"name {name!r} is invalid, reserved or duplicated")
        seen_names.add(name)

    evidence = []
    for name, spec in (data.get("evidence") or {}).items():
        claim_name(name)
        if not isinstance(spec, Mapping) or not spec.get("record_type"):
            raise fail(f"evidence {name!r} needs a record_type")
        evidence.append(EvidenceSelector(name, str(spec["record_type"]), spec.get("where")))

    derive = []
    for name, expression in (data.get("derive") or {}).items():
        claim_name(name)
        if not isinstance(expression, str) or not expression.strip():
            raise fail(f"derive {name!r} must be an expression string")
        derive.append((name, expression))

    # Outcomes: at least one, exactly one `otherwise`, and it must be last.
    raw_outcomes = data.get("outcomes") or []
    if not raw_outcomes:
        raise fail("outcomes must not be empty")
    outcomes = []
    for index, raw in enumerate(raw_outcomes):
        is_last = index == len(raw_outcomes) - 1
        is_otherwise = bool(raw.get("otherwise"))
        if is_otherwise != is_last:
            raise fail("exactly one 'otherwise' outcome is required, and it must be last")
        when = None if is_otherwise else raw.get("when")
        if not is_otherwise and (not isinstance(when, str) or not when.strip()):
            raise fail(f"outcome {index} needs a 'when' expression")
        try:
            result = Result(raw.get("result"))
        except ValueError as error:
            raise fail(f"outcome {index}: {error}") from error
        reason = str(raw.get("reason", ""))
        if not _REASON.match(reason):
            raise fail(f"outcome {index}: reason must be UPPER_SNAKE_CASE")
        outcomes.append(Outcome(when, result, reason))

    role = str(data.get("role", "check"))
    if role not in ROLES:
        raise fail(f"role must be one of {sorted(ROLES)}")
    if role == "guard":
        allowed = {Result.VERIFIED, Result.UNVERIFIABLE}
        if any(outcome.result not in allowed for outcome in outcomes):
            raise fail("a guard's outcomes may only be VERIFIED or UNVERIFIABLE")

    return CheckDefinition(
        id=check_id,
        version=version,
        kind=kind,
        level=level,
        applies_to=tuple(applies_to),
        outcomes=tuple(outcomes),
        params=tuple(params),
        evidence=tuple(evidence),
        derive=tuple(derive),
        explain=data.get("explain"),
        required=bool(data.get("required", True)),
        role=role,
    )


# --------------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------------


def claim_view(claim: Claim) -> dict[str, Any]:
    """How a claim looks inside expressions (``claim.type``, ``claim.attributes.x``, ...)."""
    return {
        "id": claim.id,
        "type": claim.type,
        "made_at": claim.made_at,
        "origin": claim.origin,
        "span": claim.span,
        "attributes": dict(claim.attributes),
    }


def episode_view(episode: Episode) -> dict[str, Any]:
    """How an episode looks inside expressions."""
    return {
        "id": episode.id,
        "kind": episode.kind,
        "agent": episode.agent,
        "opened_at": episode.opened_at,
        "closed_at": episode.closed_at,
        "attributes": dict(episode.attributes),
    }


def evidence_view(record: Evidence) -> dict[str, Any]:
    """How an evidence record looks inside expressions.

    Attributes are flattened for convenience (``c.conclusion`` rather than
    ``c.attributes.conclusion``). The standard fields are written last, so an attribute can
    never overwrite them.
    """
    return {
        **dict(record.attributes),
        "record_type": record.record_type,
        "external_ref": record.external_ref,
        "observed_at": record.observed_at,
        "occurred_at": record.occurred_at,
        "version": record.version,
    }


def evaluate_check(
    definition: CheckDefinition,
    claim: Claim,
    episode: Episode,
    evidence: Iterable[Evidence],
    *,
    now: datetime,
    evaluator: Evaluator,
    stale: frozenset[str] = frozenset(),
    params: Mapping[str, Any] | None = None,
) -> CheckRun:
    """Evaluate one check for one claim and return the frozen, hashed result.

    Never raises for data or expression problems: those produce an UNVERIFIABLE result with
    reason ENGINE_ERROR, so one broken check cannot crash a run or accuse an agent.
    """
    overrides = dict(params or {})
    records = dedupe_evidence(evidence)
    relevant_types = {selector.record_type for selector in definition.evidence}
    relevant = [r for r in records if r.record_type in relevant_types]

    # Resolve parameters first; a bad override is a configuration error, not agent failure.
    try:
        resolved_params = {p.name: p.resolve(overrides.get(p.name)) for p in definition.params}
    except CheckDefinitionError as error:
        return _error_run(definition, claim, str(error), inputs_hash="")

    # Hash every input that can influence the outcome, so results can be replayed and audited.
    inputs_hash = sha256_hex(
        {
            "check": [definition.id, definition.version],
            "claim": claim_view(claim),
            "episode": episode_view(episode),
            "evidence": [evidence_view(r) for r in relevant],
            "params": resolved_params,
            "now": now,
            "stale": sorted(stale),
        }
    )

    try:
        # Base variables every expression can use.
        activation: dict[str, Any] = {
            "claim": to_cel(claim_view(claim)),
            "episode": to_cel(episode_view(episode)),
            "params": to_cel(resolved_params),
            "now": to_cel(now),
            "stale": to_cel(sorted(stale)),
        }

        # Named evidence sets: records of the right type that pass the optional filter.
        for selector in definition.evidence:
            selected = []
            for record in relevant:
                if record.record_type != selector.record_type:
                    continue
                view = to_cel(evidence_view(record))
                if selector.where is None or evaluator.evaluate_bool(
                    selector.where, {**activation, "e": view}
                ):
                    selected.append(view)
            activation[selector.name] = celtypes.ListType(selected)

        # Derived values, in order; each may use the ones before it.
        derived: dict[str, Any] = {}
        for name, expression in definition.derive:
            value = evaluator.evaluate(expression, activation)
            activation[name] = value
            derived[name] = from_cel(value)

        # First matching outcome wins; the last one is always `otherwise`.
        for outcome in definition.outcomes:
            if outcome.when is None or evaluator.evaluate_bool(outcome.when, activation):
                return CheckRun(
                    check_id=definition.id,
                    check_version=definition.version,
                    kind=definition.kind,
                    level=definition.level,
                    claim_id=claim.id,
                    result=outcome.result,
                    reason=outcome.reason,
                    inputs_hash=inputs_hash,
                    derived=derived,
                    explanation=_render_explanation(definition.explain, derived, resolved_params),
                    role=definition.role,
                )
    except (ExpressionError, ValueError, TypeError) as error:
        return _error_run(definition, claim, str(error), inputs_hash)

    # Unreachable: validation guarantees a final `otherwise` outcome.
    raise AssertionError(f"check {definition.id} produced no outcome")


def _error_run(
    definition: CheckDefinition, claim: Claim, message: str, inputs_hash: str
) -> CheckRun:
    """A CheckRun recording that the check could not be evaluated."""
    return CheckRun(
        check_id=definition.id,
        check_version=definition.version,
        kind=definition.kind,
        level=definition.level,
        claim_id=claim.id,
        result=Result.UNVERIFIABLE,
        reason=ENGINE_ERROR,
        inputs_hash=inputs_hash,
        explanation=message,
        role=definition.role,
    )


class _KeepMissing(dict):
    """Format helper: leaves unknown ``{placeholders}`` in place instead of failing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_explanation(
    template: str | None, derived: Mapping[str, Any], params: Mapping[str, Any]
) -> str | None:
    """Fill ``{name}`` placeholders in the explanation with derived values and params."""
    if not template:
        return None
    values = _KeepMissing({**params, **derived})
    return string.Formatter().vformat(template, (), values)


@dataclass
class CompiledChecks:
    """Checks grouped by the claim type they apply to, with expressions pre-compiled."""

    by_claim_type: dict[str, list[CheckDefinition]] = field(default_factory=dict)

    @classmethod
    def build(cls, definitions: Iterable[CheckDefinition], evaluator: Evaluator) -> CompiledChecks:
        """Index checks by claim type and compile every expression, failing early on errors."""
        compiled = cls()
        seen: set[str] = set()
        for definition in definitions:
            if definition.id in seen:
                raise CheckDefinitionError(f"check {definition.id}: defined more than once")
            seen.add(definition.id)
            for expression in definition.expressions():
                try:
                    evaluator.compile(expression)
                except ExpressionError as error:
                    raise CheckDefinitionError(f"check {definition.id}: {error}") from error
            for claim_type in definition.applies_to:
                compiled.by_claim_type.setdefault(claim_type, []).append(definition)
        for claim_type, checks in compiled.by_claim_type.items():
            # A guard can withdraw a verification but never create one.
            if all(check.role == "guard" for check in checks):
                raise CheckDefinitionError(f"claim type {claim_type}: only guards apply to it")
        return compiled

    def for_claim_type(self, claim_type: str) -> list[CheckDefinition]:
        return list(self.by_claim_type.get(claim_type, []))
