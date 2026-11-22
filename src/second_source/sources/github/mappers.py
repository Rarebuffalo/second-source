"""Pure functions that turn GitHub API JSON into Second Source objects.

No network and no clock: given the same JSON they always return the same result, so they
are tested exhaustively with fixtures. The shapes produced here are the coding pack's evidence
contract (docs/packs/coding.md); keep the two in sync.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from second_source.core.model import Episode, Evidence
from second_source.packs.coding import diff_facts
from second_source.packs.coding.detection import PullRequestSignals
from second_source.packs.coding.extract import TextSource

Json = dict[str, Any]


def parse_time(value: str | None) -> datetime | None:
    """GitHub timestamps look like ``2026-11-20T09:00:00Z``; return an aware datetime."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _required_time(value: str | None, field: str) -> datetime:
    parsed = parse_time(value)
    if parsed is None:
        raise ValueError(f"GitHub response is missing {field}")
    return parsed


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    """Drop missing values: the contract expresses "no value" as an absent key."""
    return {key: value for key, value in values.items() if value is not None}


# ---- the pull request itself -----------------------------------------------------------------


def episode_from_pull(pull: Json, agent: str) -> Episode:
    repo = pull["base"]["repo"]["full_name"]
    return Episode(
        id=f"{repo.lower()}#{pull['number']}",
        kind="pull_request",
        agent=agent,
        opened_at=_required_time(pull.get("created_at"), "created_at"),
        closed_at=parse_time(pull.get("closed_at")),
        attributes=_without_none(
            {
                "repo": repo.lower(),
                "number": pull["number"],
                "url": pull.get("html_url"),
                "head_branch": pull["head"].get("ref"),
                "base_branch": pull["base"].get("ref"),
            }
        ),
    )


def signals_from_pull(pull: Json, commits: Sequence[Json]) -> PullRequestSignals:
    """What agent detection looks at: author, branch, body and commit messages."""
    return PullRequestSignals(
        author_login=(pull.get("user") or {}).get("login", ""),
        head_branch=pull["head"].get("ref") or "",
        body=pull.get("body") or "",
        commit_messages=tuple(commit["commit"].get("message") or "" for commit in commits),
    )


def pr_state_evidence(pull: Json, observed_at: datetime) -> Evidence:
    merged = bool(pull.get("merged_at"))
    return Evidence(
        record_type="pr_state",
        external_ref=str(pull["number"]),
        observed_at=observed_at,
        attributes=_without_none(
            {
                "state": pull.get("state"),
                "merged": merged,
                "merged_at": parse_time(pull.get("merged_at")),
                "merge_sha": pull.get("merge_commit_sha") if merged else None,
                # GitHub closes linked issues only for merges into the default branch.
                "base_ref": (pull.get("base") or {}).get("ref"),
                "default_branch": ((pull.get("base") or {}).get("repo") or {}).get(
                    "default_branch"
                ),
            }
        ),
    )


# ---- texts and the commit each one refers to -------------------------------------------------


def _commit_time(commit: Json) -> datetime | None:
    details = commit["commit"]
    return parse_time((details.get("committer") or {}).get("date")) or parse_time(
        (details.get("author") or {}).get("date")
    )


def ref_at(commits: Sequence[Json], when: datetime, fallback: str | None) -> str | None:
    """The newest commit dated at or before ``when``; otherwise the first commit.

    Commit dates can mislead after rebases; see docs/sources/github.md section 3.
    """
    dated = [(time, commit["sha"]) for commit in commits if (time := _commit_time(commit))]
    earlier = [item for item in dated if item[0] <= when]
    if earlier:
        return max(earlier)[1]
    if commits:
        return commits[0]["sha"]
    return fallback


def texts_from_pull(
    pull: Json, commits: Sequence[Json], comments: Sequence[Json]
) -> list[TextSource]:
    """Every text the agent wrote on the PR: title, body, commit messages, own comments."""
    opened_at = _required_time(pull.get("created_at"), "created_at")
    head_sha = pull["head"].get("sha")
    pr_ref = ref_at(commits, opened_at, head_sha)
    author = (pull.get("user") or {}).get("login")

    # Title and body can be edited after the PR opens; what we read is their latest version.
    final_ref = head_sha if head_sha and head_sha != pr_ref else None
    final_at = None
    if final_ref:
        head = next((commit for commit in commits if commit["sha"] == final_ref), None)
        final_at = (_commit_time(head) if head else None) or opened_at
    texts = [
        TextSource("title", pull.get("title") or "", opened_at, pr_ref, final_ref, final_at),
        TextSource("body", pull.get("body") or "", opened_at, pr_ref, final_ref, final_at),
    ]
    for commit in commits:
        written_at = _commit_time(commit) or opened_at
        texts.append(
            TextSource("commit", commit["commit"].get("message") or "", written_at, commit["sha"])
        )
    for comment in comments:
        # Only the PR author's comments are the agent's claims; reviewers' words are not.
        if (comment.get("user") or {}).get("login") != author:
            continue
        written_at = _required_time(comment.get("created_at"), "comment created_at")
        texts.append(
            TextSource(
                "comment",
                comment.get("body") or "",
                written_at,
                ref_at(commits, written_at, head_sha),
            )
        )
    return [text for text in texts if text.text.strip()]


# ---- CI results ------------------------------------------------------------------------------


def check_run_evidence(run: Json, ref: str, observed_at: datetime) -> Evidence:
    """One check run. Keyed by commit and job name; the run id is the version, so a re-run
    (which gets a new, larger id) replaces the older result during de-duplication."""
    return Evidence(
        record_type="check_result",
        external_ref=f"{ref}/{run['name']}",
        observed_at=observed_at,
        version=int(run["id"]),
        occurred_at=parse_time(run.get("completed_at")),
        attributes=_without_none(
            {
                "ref": ref,
                "name": run["name"],
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "source": "check_run",
                # The app that posted it (github-actions, a third-party CI, a bot). Recorded so
                # expired Actions runs can be told apart from surviving third-party checks.
                "app": (run.get("app") or {}).get("slug"),
            }
        ),
    )


# Commit statuses use a different vocabulary than check runs; map them onto it.
_STATUS_CONCLUSION = {"success": "success", "failure": "failure", "error": "failure"}


def status_evidence(status: Json, ref: str, observed_at: datetime) -> Evidence:
    """One commit status (older CI systems). Prefixed so it never collides with a check run."""
    state = status.get("state")
    return Evidence(
        record_type="check_result",
        external_ref=f"{ref}/status:{status['context']}",
        observed_at=observed_at,
        version=int(status.get("id") or 0),
        occurred_at=parse_time(status.get("updated_at")),
        attributes=_without_none(
            {
                "ref": ref,
                "name": status["context"],
                "status": "in_progress" if state == "pending" else "completed",
                "conclusion": _STATUS_CONCLUSION.get(state or ""),
                "source": "status",
            }
        ),
    )


# ---- files, issues, reverts, deployments -----------------------------------------------------


def changed_file_evidence(file: Json, observed_at: datetime) -> Evidence:
    """Paths, line counts and integrity counts. The patch (code) is read once to count skipped
    or removed tests and weakened test commands (packs/coding/diff_facts.py), then dropped."""
    path = file["filename"]
    status = file.get("status", "modified")
    return Evidence(
        record_type="changed_file",
        external_ref=path,
        observed_at=observed_at,
        attributes={
            "path": path,
            "status": status,
            "additions": int(file.get("additions", 0)),
            "deletions": int(file.get("deletions", 0)),
            **diff_facts.facts(path, status, file.get("patch")),
        },
    )


def files_summary_evidence(
    file_records: Sequence[Evidence], number: int, observed_at: datetime
) -> Evidence:
    """One record with the PR's integrity totals (CEL cannot sum per-file counts)."""
    return Evidence(
        record_type="changed_files_summary",
        external_ref=str(number),
        observed_at=observed_at,
        attributes=diff_facts.summarize([dict(record.attributes) for record in file_records]),
    )


def issue_evidence(
    issue: Json, events: Iterable[Json], repo: str, observed_at: datetime
) -> Evidence | None:
    """An issue's state. Returns None for pull requests (the issues API also lists PRs)."""
    if "pull_request" in issue:
        return None
    reopened = [
        time
        for event in events
        if event.get("event") == "reopened" and (time := parse_time(event.get("created_at")))
    ]
    return Evidence(
        record_type="issue_state",
        external_ref=f"{repo.lower()}#{issue['number']}",
        observed_at=observed_at,
        attributes=_without_none(
            {
                "repo": repo.lower(),
                "number": issue["number"],
                "state": issue.get("state"),
                "closed_at": parse_time(issue.get("closed_at")),
                "last_reopened_at": max(reopened) if reopened else None,
            }
        ),
    )


_REVERTS_COMMIT = re.compile(r"this reverts commit ([0-9a-f]{7,40})", re.IGNORECASE)
_ANY_PR_REVERT = re.compile(r"\breverts\s+[\w.-]+/[\w.-]+#\d+\b|\breverts\s+#\d+\b", re.IGNORECASE)


def revert_evidence(
    commits: Iterable[Json],
    merge_sha: str,
    repo: str,
    number: int,
    observed_at: datetime,
    title: str = "",
) -> list[Evidence]:
    """Commits on the base branch that revert the merged PR.

    Three forms are recognised:
    - ``git revert``: "This reverts commit <sha>" naming the merge commit;
    - GitHub's "Revert" button: a commit mentioning "Reverts owner/repo#<number>";
    - the Revert button followed by a squash merge, whose message may keep only the title:
      ``Revert "<PR title>"`` or ``Revert "<PR title> (#<number>)"`` (also the
      conventional-commit form ``revert: "<PR title>"``). The whole quoted title
      must match, so a short title ("Fix") cannot match an unrelated revert, and the title is
      used only when the message names no reverted PR or commit at all: an explicit
      "Reverts o/r#150" or "This reverts commit <sha>" says what was reverted, and two PRs
      may share a generic title ("Update README.md").
    """
    pr_reference = re.compile(rf"\breverts\s+{re.escape(repo)}#{number}\b", re.IGNORECASE)
    title_revert = (
        re.compile(rf'^revert:?\s+"{re.escape(title.strip())}(\s+\(#{number}\))?"', re.IGNORECASE)
        if title.strip()
        else None
    )
    found = []
    for commit in commits:
        message = commit["commit"].get("message") or ""
        named = [sha for sha in _REVERTS_COMMIT.findall(message) if merge_sha.startswith(sha)]
        explicit = _REVERTS_COMMIT.search(message) or _ANY_PR_REVERT.search(message)
        by_title = not explicit and title_revert is not None and title_revert.match(message.strip())
        if named or pr_reference.search(message) or by_title:
            found.append(
                Evidence(
                    record_type="revert",
                    external_ref=commit["sha"],
                    observed_at=observed_at,
                    occurred_at=_commit_time(commit),
                    attributes=_without_none(
                        {"reverted_sha": merge_sha, "at": _commit_time(commit)}
                    ),
                )
            )
    return found


# Files that change in almost every PR. A revert scan filtered on one of them would list many
# unrelated commits, so another changed file is preferred.
_BUSY_FILES = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|cargo\.lock|poetry\.lock|uv\.lock|"
    r"go\.sum|gemfile\.lock|composer\.lock|changelog(\.[a-z]+)?)$",
    re.IGNORECASE,
)


def revert_probe_path(files: Iterable[Json]) -> str | None:
    """One file the PR changed, to narrow the revert scan.

    A full revert touches every file the PR changed, so listing only the commits that touched
    one of them finds it with a short, exact list instead of every commit on a busy branch.
    """
    paths = sorted(file["filename"] for file in files if file.get("filename"))
    quiet = [path for path in paths if not _BUSY_FILES.search(path)]
    return (quiet or paths or [None])[0]


def deployment_evidence(
    deployment: Json, statuses: Sequence[Json], observed_at: datetime
) -> Evidence | None:
    """A deployment and its latest status (GitHub lists statuses newest first)."""
    if not statuses:
        return None
    latest = statuses[0]
    return Evidence(
        record_type="deployment_status",
        external_ref=str(deployment["id"]),
        observed_at=observed_at,
        version=int(latest.get("id") or 0),
        attributes=_without_none(
            {
                "ref": deployment.get("sha"),
                "environment": (deployment.get("environment") or "").lower(),
                "state": latest.get("state"),
                "at": parse_time(latest.get("created_at")),
            }
        ),
    )
