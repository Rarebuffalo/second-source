"""The engine: verify every claim of an episode against the available evidence.

This is the single entry point the rest of the system uses. It is pure: callers pass in the
episode, its claims, the evidence and the current time, and get verdicts back.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from second_source.core.aggregate import aggregate
from second_source.core.checks import CheckDefinition, CompiledChecks, evaluate_check
from second_source.core.expr import Evaluator, FunctionRegistry
from second_source.core.model import Claim, Episode, Evidence, Verdict
from second_source.core.pack import Pack, build_function_registry


class Engine:
    """Holds compiled checks and verifies claims.

    Create one engine per set of checks and reuse it: expressions are compiled once when the
    engine is built, so invalid checks fail immediately rather than in the middle of a run.
    """

    def __init__(
        self,
        definitions: Iterable[CheckDefinition],
        functions: FunctionRegistry | None = None,
    ) -> None:
        self._evaluator = Evaluator(functions)
        self._checks = CompiledChecks.build(definitions, self._evaluator)

    @classmethod
    def from_packs(cls, packs: Sequence[Pack]) -> Engine:
        """Build an engine from loaded packs: all their checks and helper functions."""
        definitions = [check for pack in packs for check in pack.checks]
        return cls(definitions, build_function_registry(packs))

    def checks_for(self, claim_type: str) -> list[CheckDefinition]:
        """The checks that apply to a claim type (useful for deciding what evidence to fetch)."""
        return self._checks.for_claim_type(claim_type)

    def evidence_types_for(self, claims: Iterable[Claim]) -> frozenset[str]:
        """Evidence record types needed to verify these claims.

        Sources use this to fetch only what is necessary, which keeps API usage low.
        """
        return frozenset(
            selector.record_type
            for claim in claims
            for check in self.checks_for(claim.type)
            for selector in check.evidence
        )

    def verify(
        self,
        episode: Episode,
        claims: Sequence[Claim],
        evidence: Iterable[Evidence],
        *,
        now: datetime,
        stale: frozenset[str] = frozenset(),
        params: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[Verdict]:
        """Return one verdict per claim, in the order the claims were given.

        ``stale`` lists evidence record types the source could not refresh; checks can turn
        those into UNVERIFIABLE results. ``params`` maps a check id to parameter overrides.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        for claim in claims:
            if claim.episode_id != episode.id:
                raise ValueError(f"claim {claim.id} does not belong to episode {episode.id}")

        evidence_list = list(evidence)  # may be iterated once per check
        overrides = params or {}
        verdicts = []
        for claim in claims:
            checks = self.checks_for(claim.type)
            runs = [
                evaluate_check(
                    check,
                    claim,
                    episode,
                    evidence_list,
                    now=now,
                    evaluator=self._evaluator,
                    stale=stale,
                    params=overrides.get(check.id),
                )
                for check in checks
            ]
            required = frozenset(check.id for check in checks if check.required)
            verdicts.append(aggregate(claim.id, runs, required=required))
        return verdicts
