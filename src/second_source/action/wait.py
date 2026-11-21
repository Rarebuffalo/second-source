"""Wait for CI to finish before giving a final verdict (docs/action.md, section 3).

"Tests pass" can only be judged once the PR's CI jobs complete. In check mode the Action
re-checks with growing pauses until no deciding claim is waiting on CI, or the time budget
(``wait-minutes``) runs out. After every attempt the caller publishes the current table, so the
check run shows progress. Whatever is still pending when the budget ends is picked up later by
the ``workflow_run`` event or the daily sweep.

Re-checking costs little: the GitHub client's ETag cache turns unchanged responses into
``304 Not Modified``, which do not count against the rate limit.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from second_source.action.publish import awaiting_ci
from second_source.pipeline import PullRequestReport

FIRST_PAUSE = 30.0  # seconds; doubles after each attempt
MAX_PAUSE = 120.0


def wait_for_ci(
    check: Callable[[], PullRequestReport],
    *,
    budget_seconds: float,
    on_update: Callable[[PullRequestReport], None] = lambda _report: None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> PullRequestReport:
    """Run ``check`` until CI has settled or the budget is spent; return the last report."""
    deadline = clock() + budget_seconds
    pause = FIRST_PAUSE
    report = check()
    while awaiting_ci(report):
        remaining = deadline - clock()
        if remaining <= 0:
            break
        on_update(report)
        sleep(min(pause, remaining))
        pause = min(pause * 2, MAX_PAUSE)
        report = check()
    return report
