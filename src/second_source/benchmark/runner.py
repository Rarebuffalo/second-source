"""Batch runner: check many sampled pull requests and append one result per PR (section 5).

Design points:

- **Same pipeline as the CLI.** Every published number can be reproduced with
  ``second-source check <url>``.
- **Resumable.** Results are appended to a JSON Lines file and flushed after every PR. PRs that
  already have a result are skipped, so an interrupted run simply continues.
- **Rate limits stop the run cleanly.** A PR whose checking was cut short by the rate limit is
  *not* recorded (its verdicts would be unverifiable only for lack of quota); it is retried on
  the next run.
- **Evidence is kept.** Each result stores a compact snapshot of the evidence records its
  verdicts came from (check names and conclusions, changed paths, issue states). GitHub deletes
  old workflow runs, so without it a published verdict could not be audited a year later.
- **Errors are recorded, not fatal.** A deleted PR or a broken URL becomes an error record, so
  one bad row never blocks a run, and the summary can count them.
- **Only permanent errors are final.** A deleted PR (``not_found``) or an invalid URL will not
  change, so it counts as done. Any other error (an unexpected GitHub response, often a bug on
  our side) is retried on the next run; the newer record replaces the older one when results
  are read.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from second_source.benchmark.aidev import AidevPullRequest
from second_source.pipeline import check_pull_request
from second_source.report.render import evidence_snapshot, to_json
from second_source.sources.github.client import GitHubError, RateLimited
from second_source.sources.github.source import GitHubSource, PullRequestNotFound
from second_source.sources.github.urls import parse_pull_request

RESULT_SCHEMA_VERSION = "2"  # 2: adds the evidence snapshot
# Always snapshot the PR's file list (one request), even when no claim needs it: integrity
# checks (CI config changed, tests removed) can then be re-scored offline.
SNAPSHOT_EVIDENCE = frozenset({"changed_file"})


@dataclass
class RunSummary:
    checked: int = 0
    skipped: int = 0  # already had a result from an earlier run
    errors: int = 0
    stopped: str | None = None  # why the run ended early (rate limit, limit reached)


PERMANENT_ERRORS = ("not_found", "invalid url")


def _is_final(record: dict[str, Any]) -> bool:
    """True for a checked PR or a permanent error; other errors are retried."""
    error = record.get("error")
    return error is None or str(error).startswith(PERMANENT_ERRORS)


def completed_urls(path: Path) -> set[str]:
    """URLs that already have a final result in ``path`` (an absent file means none)."""
    if not path.exists():
        return set()
    return {record["url"] for record in read_results(path) if _is_final(record)}


def run_benchmark(
    rows: Iterable[AidevPullRequest],
    out: Path,
    *,
    source: GitHubSource,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    limit: int | None = None,
    progress: Callable[[str], None] = lambda _message: None,
) -> RunSummary:
    """Check each row's PR and append the result to ``out``.

    ``limit`` caps how many PRs are checked in this run (useful for anonymous, 60-request
    runs); already-completed PRs do not count towards it.
    """
    summary = RunSummary()
    done = completed_urls(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("a", encoding="utf-8") as handle:
        for row in rows:
            if row.url in done:
                summary.skipped += 1
                continue
            if limit is not None and summary.checked + summary.errors >= limit:
                summary.stopped = f"reached the limit of {limit} pull requests"
                break

            record, stop = _check_one(row, source, clock)
            if stop:
                summary.stopped = stop
                break
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()  # a crash loses at most the PR in progress
            done.add(row.url)
            if "error" in record:
                summary.errors += 1
                progress(f"error  {row.url}: {record['error']}")
            else:
                summary.checked += 1
                progress(f"ok     {row.url} ({record['report']['api_requests']} requests)")
    return summary


def _check_one(
    row: AidevPullRequest, source: GitHubSource, clock: Callable[[], datetime]
) -> tuple[dict[str, Any], str | None]:
    """Return (record, stop reason). A stop reason means: record nothing and end the run."""
    base = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "url": row.url,
        "dataset_agent": row.agent,
    }
    try:
        ref = parse_pull_request(row.url)
    except ValueError as error:
        return {**base, "error": f"invalid url: {error}"}, None
    try:
        report = check_pull_request(ref, source, now=clock(), extra_evidence=SNAPSHOT_EVIDENCE)
    except RateLimited:
        return base, "GitHub rate limit reached"
    except PullRequestNotFound:
        return {**base, "error": "not_found"}, None
    except GitHubError as error:
        return {**base, "error": f"github: {error}"}, None
    if report.rate_limited:
        return base, "GitHub rate limit reached"
    return {**base, "report": to_json(report), "evidence": evidence_snapshot(report)}, None


def read_results(path: Path) -> list[dict[str, Any]]:
    """One record per URL, the newest one (a retried error is replaced by its later result),
    in the order URLs first appear."""
    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                latest[record["url"]] = record  # dicts keep first-insertion order
    return list(latest.values())
