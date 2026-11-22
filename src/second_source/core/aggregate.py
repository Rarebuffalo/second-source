"""Aggregation: turn the check runs for one claim into a single verdict.

The rules are conservative on purpose. A claim is only CONTRADICTED when a deterministic
check proves it false; missing or unclear evidence never counts against the agent.
"""

from __future__ import annotations

from collections.abc import Sequence

from second_source.core.checks import ENGINE_ERROR
from second_source.core.model import DETERMINISTIC_KINDS, CheckRun, Result, Verdict

# Reason used when no check applies to a claim type.
NO_APPLICABLE_CHECKS = "NO_APPLICABLE_CHECKS"
# Caveat shown when a guard could not be evaluated (it then withdraws nothing).
GUARD_ERROR = "GUARD_ERROR"


def aggregate(
    claim_id: str,
    runs: Sequence[CheckRun],
    *,
    required: frozenset[str] | None = None,
) -> Verdict:
    """Combine check runs into a verdict. The first matching rule wins:

    1. Any CONTRADICTED from a deterministic check  -> CONTRADICTED
    2. Otherwise any PENDING                         -> PENDING
    3. Otherwise any UNVERIFIABLE                    -> UNVERIFIABLE
    4. Otherwise all VERIFIED (at least one check)   -> VERIFIED
    5. No deciding checks at all                     -> UNVERIFIABLE (NO_APPLICABLE_CHECKS)

    Only *deciding* runs count: runs from deterministic kinds whose check is required.
    Judge (model) runs and optional checks are kept in the verdict for transparency but never
    change the result. ``required`` holds the ids of required checks; None means all of them.

    Guards (role "guard") can only return VERIFIED or UNVERIFIABLE, so a required guard can turn
    an all-VERIFIED claim into UNVERIFIABLE and nothing else (rules 1 and 2 still win). A guard
    that found nothing adds no reason, so a clean claim keeps its deciding checks' reasons.
    Optional guards that found something become ``caveats``: shown, never deciding.

    A guard that could not be evaluated (ENGINE_ERROR) never decides, required or not: a guard
    that cannot look must not withdraw a verification, just as stale data withdraws nothing.
    It is shown once as the caveat GUARD_ERROR.
    """

    def guard_failed(run: CheckRun) -> bool:
        return run.role == "guard" and run.reason == ENGINE_ERROR

    deciding = [
        run
        for run in runs
        if run.kind in DETERMINISTIC_KINDS
        and (required is None or run.check_id in required)
        and not guard_failed(run)
    ]

    found: list[str] = []
    for run in sorted(runs, key=lambda r: r.check_id):
        if guard_failed(run):
            reason = GUARD_ERROR
        elif (
            run.role == "guard"
            and run.result is Result.UNVERIFIABLE
            and required is not None
            and run.check_id not in required
        ):
            reason = run.reason
        else:
            continue
        if reason not in found:
            found.append(reason)
    caveats = tuple(found)

    def with_reasons(result: Result, chosen: Sequence[CheckRun]) -> Verdict:
        # Keep reasons in a stable order (by check id) so outputs are reproducible.
        reasons = tuple(run.reason for run in sorted(chosen, key=lambda r: r.check_id))
        return Verdict(
            claim_id=claim_id,
            result=result,
            reasons=reasons,
            check_runs=tuple(runs),
            caveats=caveats,
        )

    if not deciding:
        return Verdict(
            claim_id=claim_id,
            result=Result.UNVERIFIABLE,
            reasons=(NO_APPLICABLE_CHECKS,),
            check_runs=tuple(runs),
            caveats=caveats,
        )

    for result in (Result.CONTRADICTED, Result.PENDING, Result.UNVERIFIABLE):
        matching = [run for run in deciding if run.result is result]
        if matching:
            return with_reasons(result, matching)

    # Every deciding run is VERIFIED; guards that found nothing say nothing.
    return with_reasons(Result.VERIFIED, [run for run in deciding if run.role != "guard"])
