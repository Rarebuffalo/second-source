"""GitHub source: load a pull request and fetch exactly the evidence its claims need.

Two steps, so callers can extract claims in between and fetch only what is necessary:

    loaded = source.load(ref)                      # 3 API calls: PR, commits, comments
    result = source.fetch_evidence(loaded, claims, needed_types)

Failure policy (see docs/sources/github.md): a rate limit stops all further fetching and
marks every remaining record type stale; any other error marks only its own record type
stale. Stale types make checks return UNVERIFIABLE, never CONTRADICTED.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from second_source.core.model import Claim, Evidence
from second_source.packs.coding.detection import PullRequestSignals
from second_source.packs.coding.extract import (
    BUILD_OK,
    DEPLOYED,
    FIXES_ISSUE,
    LINT_OK,
    TESTS_PASS,
    TextSource,
)
from second_source.sources.github import mappers
from second_source.sources.github.client import (
    GitHubClient,
    GitHubError,
    NotFound,
    RateLimited,
)
from second_source.sources.github.urls import PullRequestRef

Json = dict[str, Any]

# Fetch order. Cheap and most decisive types first, so a rate limit hurts as little as possible.
# Reverts are looked for only inside the durability window that follows the merge (the
# coding.not_reverted check's default window_days). Without an end date, a PR merged months ago
# would make us page through every later commit and hit the page limit on any busy repository.
REVERT_WINDOW = timedelta(days=30)
REVERT_MAX_PAGES = 10  # up to 1,000 commits in the window

FETCH_ORDER = (
    "pr_state",
    "check_result",
    "changed_file",
    "changed_files_summary",
    "issue_state",
    "revert",
    "deployment_status",
)

MAX_DEPLOYMENTS_PER_REF = 5


class PullRequestNotFound(Exception):
    """The PR does not exist, or the token cannot see it."""


@dataclass(frozen=True)
class LoadedPullRequest:
    ref: PullRequestRef
    pull: Json
    commits: Sequence[Json]
    comments: Sequence[Json]
    signals: PullRequestSignals
    texts: Sequence[TextSource]

    @property
    def merged(self) -> bool:
        return bool(self.pull.get("merged_at"))


@dataclass
class EvidenceResult:
    records: list[Evidence] = field(default_factory=list)
    stale: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)  # human-readable, for the report
    # True when the rate limit cut fetching short. Batch callers stop and retry the PR later
    # instead of recording verdicts that are unverifiable only for lack of quota.
    rate_limited: bool = False


class GitHubSource:
    def __init__(
        self, client: GitHubClient, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.client = client
        self._clock = clock
        # PR files, shared by the changed_file and revert fetchers within one fetch_evidence
        # call. Reset on every call: the Action re-checks a PR after new commits.
        self._files: dict[str, list[Json]] = {}
        self._mapped: dict[str, list[Evidence]] = {}

    # ---- step 1 ------------------------------------------------------------------------------

    def load(self, ref: PullRequestRef) -> LoadedPullRequest:
        base = f"/repos/{ref.owner}/{ref.repo}"
        try:
            pull = self.client.get(f"{base}/pulls/{ref.number}")
        except NotFound as error:
            raise PullRequestNotFound(f"{ref} was not found or is not accessible") from error
        commits = self.client.get_paginated(f"{base}/pulls/{ref.number}/commits")
        comments = self.client.get_paginated(f"{base}/issues/{ref.number}/comments")
        return LoadedPullRequest(
            ref=ref,
            pull=pull,
            commits=commits,
            comments=comments,
            signals=mappers.signals_from_pull(pull, commits),
            texts=mappers.texts_from_pull(pull, commits, comments),
        )

    # ---- step 2 ------------------------------------------------------------------------------

    def fetch_evidence(
        self, loaded: LoadedPullRequest, claims: Sequence[Claim], needed: Iterable[str]
    ) -> EvidenceResult:
        """Fetch the requested evidence types; failures become stale types, not exceptions."""
        wanted = set(needed)
        result = EvidenceResult()
        self._files = {}
        self._mapped = {}
        fetchers = {
            "pr_state": self._pr_state,
            "check_result": self._check_results,
            "changed_file": self._changed_files,
            "changed_files_summary": self._files_summary,
            "issue_state": self._issue_states,
            "revert": self._reverts,
            "deployment_status": self._deployments,
        }
        pending = [kind for kind in FETCH_ORDER if kind in wanted]
        unknown = wanted - set(fetchers)
        result.stale |= unknown  # a type we cannot fetch must not be judged

        for index, kind in enumerate(pending):
            try:
                result.records.extend(fetchers[kind](loaded, claims))
            except RateLimited:
                remaining = pending[index:]
                result.stale |= set(remaining)
                result.errors.append(f"GitHub rate limit reached; not fetched: {remaining}")
                result.rate_limited = True
                break
            except GitHubError as error:  # includes PageLimitReached
                result.stale.add(kind)
                result.errors.append(f"{kind}: {error}")
        return result

    # ---- one fetcher per evidence type -------------------------------------------------------

    def _base(self, loaded: LoadedPullRequest) -> str:
        return f"/repos/{loaded.ref.owner}/{loaded.ref.repo}"

    def _pr_state(self, loaded: LoadedPullRequest, _claims: Sequence[Claim]) -> list[Evidence]:
        return [mappers.pr_state_evidence(loaded.pull, self._clock())]

    def _check_results(self, loaded: LoadedPullRequest, claims: Sequence[Claim]) -> list[Evidence]:
        records: list[Evidence] = []
        for ref in sorted(_claim_refs(claims)):
            now = self._clock()
            runs = self.client.get_paginated(
                f"{self._base(loaded)}/commits/{ref}/check-runs", {"filter": "latest"}
            )
            records.extend(mappers.check_run_evidence(run, ref, now) for run in runs)
            combined = self.client.get(f"{self._base(loaded)}/commits/{ref}/status")
            records.extend(
                mappers.status_evidence(status, ref, now) for status in combined.get("statuses", [])
            )
        return records

    def _pull_files(self, loaded: LoadedPullRequest) -> list[Json]:
        key = str(loaded.ref)
        if key not in self._files:
            self._files[key] = self.client.get_paginated(
                f"{self._base(loaded)}/pulls/{loaded.ref.number}/files", max_pages=30
            )
        return self._files[key]

    def _changed_files(self, loaded: LoadedPullRequest, _claims: Sequence[Claim]) -> list[Evidence]:
        # Mapped once per fetch: reading every patch is the costly part, and the summary
        # fetcher needs the same records.
        key = f"{loaded.ref}:records"
        if key not in self._mapped:
            now = self._clock()
            self._mapped[key] = [
                mappers.changed_file_evidence(file, now) for file in self._pull_files(loaded)
            ]
        return self._mapped[key]

    def _files_summary(self, loaded: LoadedPullRequest, claims: Sequence[Claim]) -> list[Evidence]:
        """PR-level integrity totals, from the same (shared, cached) file list."""
        files = self._changed_files(loaded, claims)
        expected = loaded.pull.get("changed_files")
        return [
            mappers.files_summary_evidence(
                files,
                loaded.ref.number,
                self._clock(),
                expected_files=int(expected) if isinstance(expected, int) else None,
            )
        ]

    def _issue_states(self, loaded: LoadedPullRequest, claims: Sequence[Claim]) -> list[Evidence]:
        records: list[Evidence] = []
        own_repo = loaded.ref.full_name
        targets = {
            (claim.attributes.get("repo") or own_repo, int(claim.attributes["issue"]))
            for claim in claims
            if claim.type == FIXES_ISSUE
        }
        for repo, number in sorted(targets):
            try:
                issue = self.client.get(f"/repos/{repo}/issues/{number}")
            except NotFound:
                continue  # inaccessible issue: the check reports ISSUE_NOT_ACCESSIBLE
            events = self.client.get_paginated(f"/repos/{repo}/issues/{number}/events")
            evidence = mappers.issue_evidence(issue, events, repo, self._clock())
            if evidence is not None:
                records.append(evidence)
        return records

    def _reverts(self, loaded: LoadedPullRequest, _claims: Sequence[Claim]) -> list[Evidence]:
        pull = loaded.pull
        if not loaded.merged or not pull.get("merge_commit_sha"):
            return []
        base = pull["base"]
        default_branch = (base.get("repo") or {}).get("default_branch")
        if default_branch and base["ref"] != default_branch:
            # Merged into a feature or release branch (often deleted since): there is no
            # meaningful branch to scan. coding.not_reverted reports NOT_DEFAULT_BRANCH.
            return []
        merged_at = mappers.parse_time(pull["merged_at"])
        until = merged_at + REVERT_WINDOW if merged_at else None
        params = {"sha": base["ref"], "since": pull["merged_at"]}
        if until is not None:
            params["until"] = until.isoformat().replace("+00:00", "Z")
        # Only commits touching one of the PR's files: a full revert must touch it too.
        path = mappers.revert_probe_path(self._pull_files(loaded))
        if path:
            params["path"] = path
        commits = self.client.get_paginated(
            f"{self._base(loaded)}/commits", params, max_pages=REVERT_MAX_PAGES
        )
        return mappers.revert_evidence(
            commits,
            pull["merge_commit_sha"],
            loaded.ref.full_name,
            loaded.ref.number,
            self._clock(),
            title=pull.get("title") or "",
        )

    def _deployments(self, loaded: LoadedPullRequest, claims: Sequence[Claim]) -> list[Evidence]:
        records: list[Evidence] = []
        refs = {claim.attributes.get("ref") for claim in claims if claim.type == DEPLOYED}
        for ref in sorted(r for r in refs if r):
            deployments = self.client.get(f"{self._base(loaded)}/deployments", {"sha": ref})
            for deployment in deployments[:MAX_DEPLOYMENTS_PER_REF]:
                statuses = self.client.get(
                    f"{self._base(loaded)}/deployments/{deployment['id']}/statuses",
                    {"per_page": 5},
                )
                evidence = mappers.deployment_evidence(deployment, statuses, self._clock())
                if evidence is not None:
                    records.append(evidence)
        return records


def _claim_refs(claims: Iterable[Claim]) -> set[str]:
    """Commits referenced by CI-dependent claims (tests, build, lint)."""
    refs: set[str] = set()
    for claim in claims:
        if claim.type in {TESTS_PASS, BUILD_OK, LINT_OK}:
            refs.update(
                ref
                for ref in (claim.attributes.get("ref"), claim.attributes.get("final_ref"))
                if ref
            )
    return refs
