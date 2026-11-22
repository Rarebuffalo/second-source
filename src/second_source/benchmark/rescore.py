"""Re-score benchmark results offline, from their evidence snapshots, with the current code.

A run that takes days can span an upgrade, so its records come from different code versions
and cannot be summarised together. Re-scoring replays the verification on each record's saved
claims and evidence, with no GitHub requests, so every published verdict comes from one version.

What it can and cannot change:

- **Claims are kept as extracted.** The snapshot has no PR text, so extraction is not re-run;
  a change to extraction needs a fresh run, not a re-score.
- **Diff counts need the diff.** Old snapshots lack the per-file integrity counts (the patch was
  never stored). For those PRs, ``fetch_file_facts`` re-reads the file list once (counts only)
  and ``rescore`` uses it; without it the guards report the files as not inspected.
- **A disagreement is reported, never hidden.** Claim times are not in the snapshot (the claim's
  time is rebuilt from the PR's open or merge time), so a time-sensitive check could in
  principle decide differently. Only two outcomes are accepted: the same result as before, or a
  VERIFIED withdrawn by a guard. Anything else keeps the stored result and is marked
  ``rescore_mismatch`` so it can be investigated; the summary must report their number.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from second_source import __version__
from second_source.core.engine import Engine
from second_source.core.model import Claim, Episode, Evidence, Result
from second_source.pipeline import default_engine
from second_source.report.render import WORDS, _plain, withdrawn_reasons
from second_source.sources.github import mappers
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.urls import parse_pull_request

FILE_EVIDENCE = "changed_file"
SUMMARY_EVIDENCE = "changed_files_summary"
MAX_FILE_PAGES = 30  # the same 3,000-file cap as a normal check


def needs_file_facts(record: Mapping[str, Any], engine: Engine | None = None) -> bool:
    """True when guards could change this record but its snapshot has no diff counts.

    Guards only act on a VERIFIED claim, so only records with a VERIFIED claim of a guarded
    type need the diff.
    """
    if "report" not in record:
        return False
    files = [e for e in record.get("evidence", []) if e["type"] == FILE_EVIDENCE]
    if files and all("patch_available" in e["attributes"] for e in files):
        return False
    engine = engine or default_engine()
    return any(
        claim["result"] == Result.VERIFIED.value and _has_guard(engine, claim["type"])
        for claim in record["report"]["claims"]
    )


def _has_guard(engine: Engine, claim_type: str) -> bool:
    return any(check.role == "guard" for check in engine.checks_for(claim_type))


def fetch_file_facts(
    client: GitHubClient, url: str, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> dict[str, Any]:
    """The PR's changed files reduced to counts (one paginated request; no code is kept)."""
    ref = parse_pull_request(url)
    observed_at = clock()
    files = client.get_paginated(
        f"/repos/{ref.full_name}/pulls/{ref.number}/files", max_pages=MAX_FILE_PAGES
    )
    return {
        "url": url,
        "observed_at": observed_at.isoformat(),
        "files": [
            dict(mappers.changed_file_evidence(file, observed_at).attributes) for file in files
        ],
    }


def rescore(
    record: Mapping[str, Any],
    *,
    file_facts: Mapping[str, Any] | None = None,
    engine: Engine | None = None,
    pack_version: str,
) -> dict[str, Any]:
    """The record with its verdicts recomputed by the current code (see the module notes)."""
    if "report" not in record:
        return dict(record)
    engine = engine or default_engine()
    report = record["report"]
    checked_at = _time(report["checked_at"])
    evidence = _evidence(record["evidence"], checked_at, report["pull_request"]["ref"], file_facts)
    episode = _episode(report, record["dataset_agent"])
    stored = report["claims"]
    claims = [_claim(item, episode, evidence) for item in stored]
    verdicts = engine.verify(
        episode, claims, evidence, now=checked_at, stale=frozenset(report.get("stale", []))
    )

    new_claims = []
    for item, verdict in zip(stored, verdicts, strict=True):
        withdrawn = withdrawn_reasons(verdict)
        accepted = verdict.result.value == item["result"] or (
            item["result"] == Result.VERIFIED.value
            and verdict.result is Result.UNVERIFIABLE
            and withdrawn
        )
        if not accepted:
            new_claims.append({**item, "rescore_mismatch": verdict.result.value})
            continue
        new_claims.append(
            {
                **item,
                "result": verdict.result.value,
                "reasons": list(verdict.reasons),
                "withdrawn": list(withdrawn),
                "caveats": list(verdict.caveats),
                "checks": [
                    {
                        "id": run.check_id,
                        "version": run.check_version,
                        "level": run.level.value,
                        "role": run.role,
                        "result": run.result.value,
                        "reason": run.reason,
                        "explanation": run.explanation,
                        "inputs_hash": run.inputs_hash,
                    }
                    for run in verdict.check_runs
                ],
            }
        )
    counts = Counter(Result(claim["result"]) for claim in new_claims)
    return {
        **record,
        "tool_version": __version__,
        "pack_version": pack_version,
        "rescored_from": {
            "tool_version": record.get("tool_version"),
            "pack_version": record.get("pack_version"),
        },
        "report": {
            **report,
            "claims": new_claims,
            "summary": {WORDS[result]: count for result, count in counts.items()},
        },
        "evidence": [_snapshot_item(e) for e in evidence],
    }


def rescore_all(
    records: Iterable[Mapping[str, Any]],
    facts_by_url: Mapping[str, Mapping[str, Any]],
    *,
    pack_version: str,
) -> tuple[list[dict[str, Any]], int]:
    """Every record re-scored, and how many claims kept their stored result (mismatches)."""
    engine = default_engine()
    out = [
        rescore(r, file_facts=facts_by_url.get(r["url"]), engine=engine, pack_version=pack_version)
        for r in records
    ]
    mismatches = sum(
        "rescore_mismatch" in claim for r in out if "report" in r for claim in r["report"]["claims"]
    )
    return out, mismatches


# ---- rebuilding the engine's inputs from a snapshot -----------------------------------------


def _evidence(
    snapshot: Iterable[Mapping[str, Any]],
    checked_at: datetime,
    pr_ref: str,
    file_facts: Mapping[str, Any] | None,
) -> list[Evidence]:
    records = [
        Evidence(
            record_type=item["type"],
            external_ref=item["ref"],
            observed_at=checked_at,
            attributes=_revived(item["attributes"]),
            occurred_at=_time(item["occurred_at"]) if item.get("occurred_at") else None,
        )
        for item in snapshot
        # The summary is always rebuilt below, from the file counts the current code produced.
        if item["type"] != SUMMARY_EVIDENCE
    ]
    if file_facts is not None:
        observed_at = _time(file_facts["observed_at"])
        records = [r for r in records if r.record_type != FILE_EVIDENCE] + [
            Evidence(FILE_EVIDENCE, attrs["path"], observed_at, _revived(attrs))
            for attrs in file_facts["files"]
        ]
    files = [r for r in records if r.record_type == FILE_EVIDENCE]
    old_summary = next((i for i in snapshot if i["type"] == SUMMARY_EVIDENCE), None)
    if files or old_summary is not None:
        records.append(
            mappers.files_summary_evidence(files, int(pr_ref.rsplit("#", 1)[1]), checked_at)
        )
    return records


def _episode(report: Mapping[str, Any], dataset_agent: str) -> Episode:
    ref = report["pull_request"]["ref"]
    repo, number = ref.lower().rsplit("#", 1)
    agent = (report.get("agent") or {}).get("name") or dataset_agent
    return Episode(
        id=ref.lower(),
        kind="pull_request",
        agent=agent,
        opened_at=_time(report["pull_request"]["opened_at"]),
        # What checks read from the episode (mappers.episode_from_pull sets the same keys).
        attributes={"repo": repo, "number": int(number), "url": report["pull_request"]["url"]},
    )


def _claim(item: Mapping[str, Any], episode: Episode, evidence: list[Evidence]) -> Claim:
    return Claim(
        id=item["id"],
        episode_id=episode.id,
        type=item["type"],
        made_at=_made_at(item, episode, evidence),
        attributes=_revived(item["attributes"]),
        origin=item["origin"],
        span=item["span"],
    )


def _made_at(item: Mapping[str, Any], episode: Episode, evidence: list[Evidence]) -> datetime:
    """The claim's time, rebuilt: merge claims at the merge, text claims at the PR's opening
    (a commit or comment claim was later; a mismatch check guards against the difference)."""
    if item["origin"] == "merge":
        state = next((e for e in evidence if e.record_type == "pr_state"), None)
        merged_at = state.attributes.get("merged_at") if state else None
        if isinstance(merged_at, datetime):
            return merged_at
    return episode.opened_at


def _snapshot_item(record: Evidence) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": record.record_type,
        "ref": record.external_ref,
        "attributes": {key: _plain(value) for key, value in record.attributes.items()},
    }
    if record.occurred_at is not None:
        item["occurred_at"] = record.occurred_at.isoformat()
    return item


# Snapshots store times as ISO strings (render._plain); checks compare them as timestamps.
_ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})")


def _revived(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Attributes with ISO timestamp strings made datetimes again, as the mappers made them."""
    return {
        key: _time(value) if isinstance(value, str) and _ISO_TIME.fullmatch(value) else value
        for key, value in attributes.items()
    }


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
