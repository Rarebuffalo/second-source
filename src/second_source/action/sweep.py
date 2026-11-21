"""Daily sweep: find recent agent PRs to re-check (docs/action.md, section 2).

Durability claims ("the merge was not reverted", "the issue stayed closed") are judged over the
30 days after a merge, and pending claims need a later look. A scheduled run re-checks every
agent PR updated in the last ``sweep_days`` days; most re-checks are cheap because unchanged
responses come back as ``304 Not Modified`` from the ETag cache.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from second_source.packs.coding.detection import detect_agent
from second_source.sources.github import mappers
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.urls import PullRequestRef

MAX_PAGES = 5  # up to 500 recently updated PRs per run


def recent_pull_requests(
    client: GitHubClient,
    repository: str,
    *,
    now: datetime,
    days: int,
    only_agents: bool = True,
) -> list[PullRequestRef]:
    """PRs updated within ``days``, newest first; only agent PRs unless told otherwise.

    Agent detection here uses the PR alone (author, branch, body), without commit messages, to
    keep the sweep at a few requests. A PR found only through a commit trailer is still checked
    on its own events.
    """
    owner, name = repository.split("/", 1)
    cutoff = now - timedelta(days=days)
    refs = []
    # Newest-updated first, so reading stops at the first PR older than the cutoff.
    for page in range(1, MAX_PAGES + 1):
        params = {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100}
        pulls = client.get(f"/repos/{owner}/{name}/pulls", {**params, "page": page})
        for pull in pulls:
            updated = mappers.parse_time(pull.get("updated_at"))
            if updated is None or updated < cutoff:
                return refs
            if only_agents and detect_agent(mappers.signals_from_pull(pull, [])) is None:
                continue
            refs.append(PullRequestRef(owner, name, int(pull["number"])))
        if len(pulls) < 100:
            break
    return refs
