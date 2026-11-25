"""Daily sweep: find recent agent PRs to re-check (docs/action.md, section 2).

Durability claims ("the merge was not reverted", "the issue stayed closed") are judged over the
30 days after a merge, and pending claims need a later look. A scheduled run re-checks every
agent PR updated in the last ``sweep_days`` days.

Each re-check costs roughly 5-15 API requests: the response cache lives only for one run, so
nothing is free across runs. ``GITHUB_TOKEN`` allows about 1,000 requests an hour per
repository, so the run stops sweeping while ``SWEEP_RESERVE`` requests are still left for the
repository's PR events. Each day's sweep starts at a different place in the list
(``rotated``), so on a busy repository every PR is reached over successive days instead of
the same newest ones every time.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from second_source.packs.coding.detection import detect_agent
from second_source.sources.github import mappers
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.urls import PullRequestRef

MAX_PAGES = 5  # up to 500 recently updated PRs per run
SWEEP_RESERVE = 200  # requests left untouched for PR events while the sweep runs
# PRs whose own text shows no agent get their commits read (one request each), up to this
# many per sweep, to find agents known only from a commit trailer ("Co-Authored-By: Claude").
MAX_COMMIT_LOOKUPS = 30


def rotated(refs: list[PullRequestRef], day: int) -> list[PullRequestRef]:
    """``refs`` starting at a position that moves with the day, wrapping around, so a sweep cut
    short reaches different PRs each day. The stride is coprime with the list length, so over
    ``len(refs)`` days every PR comes first once."""
    if not refs:
        return refs
    stride = next(s for s in (37, 41, 43) if math.gcd(s, len(refs)) == 1)
    start = (day * stride) % len(refs)
    return refs[start:] + refs[:start]


def recent_pull_requests(
    client: GitHubClient,
    repository: str,
    *,
    now: datetime,
    days: int,
    only_agents: bool = True,
) -> list[PullRequestRef]:
    """PRs updated within ``days``, newest first; only agent PRs unless told otherwise.

    Agent detection uses the PR itself (author, branch, body) and, for up to
    ``MAX_COMMIT_LOOKUPS`` PRs that show nothing, its commit messages: an agent known only from
    a commit trailer would otherwise never have its durability re-checked.
    """
    owner, name = repository.split("/", 1)
    cutoff = now - timedelta(days=days)
    refs = []
    lookups = 0
    # Newest-updated first, so reading stops at the first PR older than the cutoff.
    for page in range(1, MAX_PAGES + 1):
        params = {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100}
        pulls = client.get(f"/repos/{owner}/{name}/pulls", {**params, "page": page})
        for pull in pulls:
            updated = mappers.parse_time(pull.get("updated_at"))
            if updated is None or updated < cutoff:
                return refs
            if only_agents and detect_agent(mappers.signals_from_pull(pull, [])) is None:
                if lookups >= MAX_COMMIT_LOOKUPS:
                    continue
                lookups += 1
                commits = client.get(f"/repos/{owner}/{name}/pulls/{pull['number']}/commits")
                if detect_agent(mappers.signals_from_pull(pull, commits)) is None:
                    continue
            refs.append(PullRequestRef(owner, name, int(pull["number"])))
        if len(pulls) < 100:
            break
    return refs
