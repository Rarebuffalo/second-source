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
    # Workflow runs before check runs: a workflow seen unfinished keeps the claim waiting, and
    # its check runs read afterwards are at least as new. It also finds Second Source's own
    # runs, whose check runs are then left out.
    "ci_workflow_run",
    "check_result",
    "changed_file",
    "changed_files_summary",
    "issue_state",
    "revert",
    "deployment_status",
)

MAX_DEPLOYMENTS_PER_REF = 5
# PR text is attacker-controlled, and each distinct reference costs requests from the
# repository's shared hourly quota (security review 1, H3: one body of "Fixes #N" lines made
# 5,000 requests). Up to these caps, references are fetched in priority order: the PR body
# and title first, then commit messages, then comments, each in the order written. The rest
# are not fetched, so their claims find no evidence and are UNVERIFIABLE; the report says how
# many were left out. Adding references can therefore never hide the body's own claims
# (security review 2, N2: a whole-type cap let 21 decoy references hide every issue claim).
MAX_ISSUES_PER_PR = 20  # two requests each
MAX_CI_REFS_PER_PR = 8  # three requests each (check runs, statuses, workflow runs)
MAX_DEPLOY_REFS_PER_PR = 4  # up to six requests each


_ORIGIN_RANK = {"body": 0, "title": 0, "commit": 1, "comment": 2}


def _priority(claim: Claim) -> tuple[int, Any]:
    return _ORIGIN_RANK.get(claim.origin, 3), claim.made_at


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

    @property
    def repo(self) -> str:
        """The repository's current name. A renamed or transferred repository still answers at
        its old URL, but the episode (mappers.episode_from_pull) uses the current name, so
        evidence about it must too, or checks would not find their own repository's records."""
        return ((self.pull.get("base") or {}).get("repo") or {}).get(
            "full_name"
        ) or self.ref.full_name


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
        self,
        client: GitHubClient,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        *,
        own_workflow_path: str | None = None,
    ) -> None:
        """``own_workflow_path`` (the Action passes its workflow file, for example
        ``.github/workflows/second-source.yml``): runs of that workflow, and their check runs,
        are Second Source itself, never CI evidence, whatever the workflow or job is named."""
        self.client = client
        self._clock = clock
        self._own_workflow_path = own_workflow_path
        self._own_suites: set[int] = set()
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
        self._left_out: dict[str, int] = {}
        fetchers = {
            "pr_state": self._pr_state,
            "check_result": self._check_results,
            "ci_workflow_run": self._workflow_runs,
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
        result.errors += [
            f"{count} {what} not checked (more than the per-PR limit)"
            for what, count in self._left_out.items()
        ]
        return result

    def _first(self, keyed: Iterable[tuple[Any, Claim]], limit: int, what: str) -> list[Any]:
        """Distinct keys in claim priority order, at most ``limit``; counts the rest."""
        ordered: list[Any] = []
        for key, _claim in sorted(keyed, key=lambda pair: _priority(pair[1])):
            if key not in ordered:
                ordered.append(key)
        if len(ordered) > limit:
            self._left_out[what] = len(ordered) - limit
        return ordered[:limit]

    def _ci_refs(self, claims: Sequence[Claim]) -> list[str]:
        keyed = [
            (ref, claim)
            for claim in claims
            if claim.type in {TESTS_PASS, BUILD_OK, LINT_OK}
            for ref in (claim.attributes.get("ref"), claim.attributes.get("final_ref"))
            if ref
        ]
        return self._first(keyed, MAX_CI_REFS_PER_PR, "commits with CI claims")

    # ---- one fetcher per evidence type -------------------------------------------------------

    def _base(self, loaded: LoadedPullRequest) -> str:
        return f"/repos/{loaded.ref.owner}/{loaded.ref.repo}"

    def _pr_state(self, loaded: LoadedPullRequest, _claims: Sequence[Claim]) -> list[Evidence]:
        return [mappers.pr_state_evidence(loaded.pull, self._clock())]

    def _check_results(self, loaded: LoadedPullRequest, claims: Sequence[Claim]) -> list[Evidence]:
        records: list[Evidence] = []
        for ref in self._ci_refs(claims):
            now = self._clock()
            runs = self.client.get_paginated(
                f"{self._base(loaded)}/commits/{ref}/check-runs", {"filter": "latest"}
            )
            records.extend(
                mappers.check_run_evidence(run, ref, now)
                for run in runs
                if (run.get("check_suite") or {}).get("id") not in self._own_suites
            )
            combined = self.client.get(f"{self._base(loaded)}/commits/{ref}/status")
            records.extend(
                mappers.status_evidence(status, ref, now) for status in combined.get("statuses", [])
            )
        return records

    def _workflow_runs(self, loaded: LoadedPullRequest, claims: Sequence[Claim]) -> list[Evidence]:
        records: list[Evidence] = []
        for ref in self._ci_refs(claims):
            now = self._clock()
            runs = self.client.get_paginated(
                f"{self._base(loaded)}/actions/runs", {"head_sha": ref}, max_pages=3
            )
            for run in runs:
                if self._own_workflow_path and run.get("path") == self._own_workflow_path:
                    self._own_suites.add(run.get("check_suite_id"))
                    continue
                records.append(mappers.workflow_run_evidence(run, ref, now))
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
        own_repo = loaded.repo
        keyed = [
            ((claim.attributes.get("repo") or own_repo, int(claim.attributes["issue"])), claim)
            for claim in claims
            if claim.type == FIXES_ISSUE
        ]
        for repo, number in self._first(keyed, MAX_ISSUES_PER_PR, "issue references"):
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
            other_names=(loaded.repo,),
        )

    def _deployments(self, loaded: LoadedPullRequest, claims: Sequence[Claim]) -> list[Evidence]:
        records: list[Evidence] = []
        keyed = [
            (claim.attributes["ref"], claim)
            for claim in claims
            if claim.type == DEPLOYED and claim.attributes.get("ref")
        ]
        for ref in self._first(keyed, MAX_DEPLOY_REFS_PER_PR, "commits with deploy claims"):
            deployments = self.client.get(f"{self._base(loaded)}/deployments", {"sha": ref})
            # GitHub lists deployments newest first; past the cap, a success may be unseen.
            complete = len(deployments) <= MAX_DEPLOYMENTS_PER_REF
            for deployment in deployments[:MAX_DEPLOYMENTS_PER_REF]:
                statuses = self.client.get(
                    f"{self._base(loaded)}/deployments/{deployment['id']}/statuses",
                    {"per_page": 5},
                )
                evidence = mappers.deployment_evidence(
                    deployment, statuses, self._clock(), complete=complete
                )
                if evidence is not None:
                    records.append(evidence)
        return records
