"""Application pipeline: check one pull request end to end.

    load PR  ->  detect agent  ->  extract claims (+ implicit merge claim)
             ->  fetch only the evidence those claims need  ->  verify  ->  report

Used by the CLI, the GitHub Action and the benchmark, so all three give identical verdicts
for the same pull request.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from second_source.core.engine import Engine
from second_source.core.model import Claim, Episode, Evidence, Verdict
from second_source.packs import coding
from second_source.packs.coding.detection import AgentMatch, detect_agent
from second_source.packs.coding.extract import extract_claims, merge_claim
from second_source.sources.github import mappers
from second_source.sources.github.source import GitHubSource
from second_source.sources.github.urls import PullRequestRef

UNKNOWN_AGENT = "unknown"


@dataclass(frozen=True)
class PullRequestReport:
    ref: PullRequestRef
    episode: Episode
    agent: AgentMatch | None  # None when no agent signal was found (likely a human PR)
    claims: Sequence[Claim]
    verdicts: Sequence[Verdict]
    stale: frozenset[str]
    errors: Sequence[str]
    checked_at: datetime
    api_requests: int
    rate_limited: bool = False  # evidence fetching stopped at the rate limit (see stale)
    head_sha: str = ""  # the PR's head commit, where the GitHub Action attaches its check run
    labels: tuple[str, ...] = ()  # PR labels (the Action records disputes as a label)
    evidence: tuple[Evidence, ...] = ()  # the records the verdicts were computed from
    # Evidence types requested from the source: a type absent here was never fetched, which a
    # later re-score must not read as "fetched and empty".
    fetched: frozenset[str] = frozenset()

    def verdict_for(self, claim: Claim) -> Verdict:
        return next(v for v in self.verdicts if v.claim_id == claim.id)


@lru_cache(maxsize=1)
def default_engine() -> Engine:
    """The engine with the bundled coding pack (built once per process)."""
    return Engine.from_packs([coding.load()])


def check_pull_request(
    ref: PullRequestRef,
    source: GitHubSource,
    *,
    engine: Engine | None = None,
    now: datetime | None = None,
    extra_evidence: frozenset[str] = frozenset(),
) -> PullRequestReport:
    """Verify every claim in a pull request. Raises PullRequestNotFound for missing PRs.

    ``extra_evidence`` names evidence types to fetch even when no claim needs them. The
    benchmark uses it to keep every PR's file list in its evidence snapshot, so integrity
    signals (CI config or tests changed) can be re-scored later without new requests.
    """
    engine = engine or default_engine()
    checked_at = now or datetime.now(UTC)
    # The client may be shared across many PRs (benchmark runs), so count this PR's requests
    # as a difference rather than reading the client's running total.
    requests_before = source.client.stats.requests

    loaded = source.load(ref)
    agent = detect_agent(loaded.signals)
    episode = mappers.episode_from_pull(loaded.pull, agent.agent if agent else UNKNOWN_AGENT)

    claims = extract_claims(episode.id, loaded.texts, repo=ref.full_name)
    merged_at = mappers.parse_time(loaded.pull.get("merged_at"))
    merge_sha = loaded.pull.get("merge_commit_sha")
    if merged_at and merge_sha:
        # Merging asserts the change is good; durability checks verify it over time.
        claims.append(merge_claim(episode.id, merged_at, merge_sha))

    needed = set(engine.evidence_types_for(claims)) | set(extra_evidence)
    evidence = source.fetch_evidence(loaded, claims, needed)
    verdicts = engine.verify(
        episode, claims, evidence.records, now=checked_at, stale=frozenset(evidence.stale)
    )
    return PullRequestReport(
        ref=ref,
        episode=episode,
        agent=agent,
        claims=claims,
        verdicts=verdicts,
        stale=frozenset(evidence.stale),
        errors=tuple(evidence.errors),
        checked_at=checked_at,
        api_requests=source.client.stats.requests - requests_before,
        rate_limited=evidence.rate_limited,
        head_sha=(loaded.pull.get("head") or {}).get("sha", ""),
        labels=tuple(label["name"] for label in loaded.pull.get("labels", [])),
        evidence=tuple(evidence.records),
        fetched=frozenset(needed),
    )
