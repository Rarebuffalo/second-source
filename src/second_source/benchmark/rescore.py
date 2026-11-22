"""Re-score benchmark results offline, from their evidence snapshots, with the current code.

A run that takes days can span an upgrade, so its records come from different code versions
and cannot be summarised together. Re-scoring replays the verification on each record's saved
claims and evidence, with no GitHub requests, so every published verdict comes from one version.

What it can and cannot change:

- **Claims are kept as extracted.** The snapshot has no PR text, so extraction is not re-run;
  a change to extraction needs a fresh run, not a re-score.
- **Diff counts must come from the current code.** Per-file counts (skips, test cases, gates)
  are made once, when the patch is read, and the patch is never stored. A record whose counts
  carry another fingerprint (``diff_facts.FACTS_VERSION``), or none, needs its file list
  re-read: ``fetch_file_facts`` does that (counts only), and also records the repository's
  current name and file count. Without it, stale counts are dropped and the guards report the
  files as not inspected, never as clean.
- **Inputs a snapshot lacks are rebuilt, and checked.** Records written before snapshots kept
  claim times and evidence versions get them rebuilt (the PR's open or merge time; first
  record wins), so every required check whose version is unchanged must reproduce its stored
  result and reason. Only guards (whose inputs are the re-read diff counts) and checks with a
  new version (a deliberate change of rules) may decide differently. A claim that fails this
  becomes UNVERIFIABLE (reason ``RESCORE_MISMATCH``, never a verdict no run firmly supports),
  keeps both results
  (``stored_result``, ``rescore_mismatch``) for investigation, and the summary counts it. A
  record that cannot be rebuilt at all gives ``RESCORE_ERROR`` on every claim.
- **Re-score the original results, never a re-scored file**: its stored results are no longer
  the original run's.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from second_source import __version__
from second_source.core.engine import Engine
from second_source.core.model import Claim, Episode, Evidence, Result, Verdict
from second_source.packs.coding import diff_facts
from second_source.pipeline import default_engine
from second_source.report.render import WORDS, _plain, withdrawn_reasons
from second_source.sources.github import mappers
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.urls import parse_pull_request

FILE_EVIDENCE = "changed_file"
SUMMARY_EVIDENCE = "changed_files_summary"
MAX_FILE_PAGES = 30  # the same 3,000-file cap as a normal check
RESCORE_MISMATCH = "RESCORE_MISMATCH"
RESCORE_ERROR = "RESCORE_ERROR"


class AlreadyRescored(ValueError):
    """The input is a re-scored file; its stored results are not the original run's."""


def needs_file_facts(record: Mapping[str, Any]) -> bool:
    """True when the record's diff counts were not made by the current code."""
    if "report" not in record:
        return False
    return _facts_version(record.get("evidence", [])) != diff_facts.FACTS_VERSION


def _facts_version(snapshot: Iterable[Mapping[str, Any]]) -> str | None:
    summary = next((i for i in snapshot if i["type"] == SUMMARY_EVIDENCE), None)
    return summary["attributes"].get("facts_version") if summary else None


def fetch_file_facts(
    client: GitHubClient, url: str, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> dict[str, Any]:
    """The PR's changed files reduced to counts by the current code (no code is kept), its
    repository's current name and its file count (two requests, plus file-list pages)."""
    ref = parse_pull_request(url)
    observed_at = clock()
    pull = client.get(f"/repos/{ref.full_name}/pulls/{ref.number}")
    files = client.get_paginated(
        f"/repos/{ref.full_name}/pulls/{ref.number}/files", max_pages=MAX_FILE_PAGES
    )
    return {
        "url": url,
        "observed_at": observed_at.isoformat(),
        "facts_version": diff_facts.FACTS_VERSION,
        "repo": ((pull.get("base") or {}).get("repo") or {}).get("full_name"),
        "changed_files": pull.get("changed_files"),
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
    if "rescored_from" in record:
        raise AlreadyRescored(f"{record.get('url')} was already re-scored")
    if "report" not in record:
        return dict(record)
    engine = engine or default_engine()
    if file_facts is not None and file_facts.get("facts_version") != diff_facts.FACTS_VERSION:
        file_facts = None  # made by other code: as stale as the snapshot's own counts
    try:
        new_claims, evidence = _replay(record, file_facts, engine)
    except (KeyError, ValueError, TypeError) as error:
        return _failed(record, f"{type(error).__name__}: {error}", pack_version)
    counts = Counter(Result(claim["result"]) for claim in new_claims)
    report = record["report"]
    return {
        **record,
        **_versions(record, pack_version),
        "report": {
            **report,
            "claims": new_claims,
            "summary": {WORDS[result]: count for result, count in counts.items()},
        },
        "evidence": [_snapshot_item(e) for e in evidence],
    }


def _replay(
    record: Mapping[str, Any], file_facts: Mapping[str, Any] | None, engine: Engine
) -> tuple[list[dict[str, Any]], list[Evidence]]:
    report = record["report"]
    checked_at = _time(report["checked_at"])
    episode = _episode(report, record["dataset_agent"], file_facts)
    evidence = _evidence(record["evidence"], checked_at, episode, file_facts)
    stored = report["claims"]
    claims = [_claim(item, episode, evidence) for item in stored]
    stale = set(report.get("stale", []))
    if "fetched" in report:
        # A type the original run never asked for is unknown, not empty.
        stale |= (
            set(engine.evidence_types_for(claims))
            - set(report["fetched"])
            - {
                SUMMARY_EVIDENCE  # rebuilt from the file list
            }
        )
    verdicts = engine.verify(episode, claims, evidence, now=checked_at, stale=frozenset(stale))
    required = {
        check.id: check.required for claim in claims for check in engine.checks_for(claim.type)
    }
    new_claims = []
    for item, verdict in zip(stored, verdicts, strict=True):
        if _reproduces(item, verdict, required):
            new_claims.append(_claim_json(item, verdict))
        else:
            new_claims.append(_unreproduced(item, RESCORE_MISMATCH, verdict.result.value))
    return new_claims, evidence


def _reproduces(item: Mapping[str, Any], verdict: Verdict, required: Mapping[str, bool]) -> bool:
    """True when every required run at an unchanged version gives the stored result and reason,
    and every run without a stored counterpart is a guard (new since the original run)."""
    before = {(run["id"], run["version"]): run for run in item.get("checks", [])}
    stored_ids = {run["id"] for run in item.get("checks", [])}
    for run in verdict.check_runs:
        if not required.get(run.check_id, True) or run.role == "guard":
            # Optional runs only add cautions. Guards read only diff counts, which are either
            # the current code's (re-read) or dropped (not inspected), never claim times, so
            # their new result is the current code's answer; and they cannot contradict.
            continue
        old = before.get((run.check_id, run.check_version))
        if old is not None:
            if (old["result"], old["reason"]) != (run.result.value, run.reason):
                return False
        elif run.check_id not in stored_ids and run.role != "guard":
            return False  # a deciding check the original run did not have
    return True


def _claim_json(item: Mapping[str, Any], verdict: Verdict) -> dict[str, Any]:
    return {
        **item,
        "result": verdict.result.value,
        "reasons": list(verdict.reasons),
        "withdrawn": list(withdrawn_reasons(verdict)),
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


def _unreproduced(item: Mapping[str, Any], reason: str, new_result: str) -> dict[str, Any]:
    """Neither run can be trusted: unverifiable, with both results kept for investigation."""
    return {
        **item,
        "result": Result.UNVERIFIABLE.value,
        "reasons": [reason],
        "withdrawn": [],
        "caveats": [],
        "stored_result": item["result"],
        "rescore_mismatch": new_result,
    }


def _failed(record: Mapping[str, Any], error: str, pack_version: str) -> dict[str, Any]:
    claims = [_unreproduced(item, RESCORE_ERROR, "ERROR") for item in record["report"]["claims"]]
    return {
        **record,
        **_versions(record, pack_version),
        "rescore_error": error,
        "report": {**record["report"], "claims": claims, "summary": {"unverifiable": len(claims)}},
    }


def _versions(record: Mapping[str, Any], pack_version: str) -> dict[str, Any]:
    return {
        "tool_version": __version__,
        "pack_version": pack_version,
        "rescored_from": {
            "tool_version": record.get("tool_version"),
            "pack_version": record.get("pack_version"),
        },
    }


def rescore_all(
    records: Iterable[Mapping[str, Any]],
    facts_by_url: Mapping[str, Mapping[str, Any]],
    *,
    pack_version: str,
) -> tuple[list[dict[str, Any]], int]:
    """Every record re-scored, and how many claims could not be reproduced (mismatches)."""
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
    episode: Episode,
    file_facts: Mapping[str, Any] | None,
) -> list[Evidence]:
    snapshot = list(snapshot)
    current = _facts_version(snapshot) == diff_facts.FACTS_VERSION
    records = []
    for item in snapshot:
        if item["type"] == SUMMARY_EVIDENCE:
            continue  # always rebuilt below, from the file counts
        attributes = _revived(item["attributes"])
        if item["type"] == FILE_EVIDENCE and not current:
            # Counts made by other code are not trusted: keep the path facts, drop the rest,
            # so the guards see the file as not inspected.
            attributes = {k: attributes[k] for k in _PATH_FACTS if k in attributes}
        records.append(
            Evidence(
                record_type=item["type"],
                external_ref=item["ref"],
                observed_at=_time(item["observed_at"]) if "observed_at" in item else checked_at,
                attributes=attributes,
                occurred_at=_time(item["occurred_at"]) if item.get("occurred_at") else None,
                version=int(item.get("version", 1)),
            )
        )
    expected = None
    if file_facts is not None:
        observed_at = _time(file_facts["observed_at"])
        records = [r for r in records if r.record_type != FILE_EVIDENCE] + [
            Evidence(FILE_EVIDENCE, attrs["path"], observed_at, _revived(attrs))
            for attrs in file_facts["files"]
        ]
        if isinstance(file_facts.get("changed_files"), int):
            expected = file_facts["changed_files"]
    elif current:
        # Keep the original run's count of files GitHub never listed (the 3,000-file cap).
        old = next(i for i in snapshot if i["type"] == SUMMARY_EVIDENCE)["attributes"]
        listed = sum(1 for i in snapshot if i["type"] == FILE_EVIDENCE)
        unlisted = old.get("uninspected", 0) - sum(
            1
            for i in snapshot
            if i["type"] == FILE_EVIDENCE and not i["attributes"].get("patch_available")
        )
        expected = listed + max(unlisted, 0)
    files = [r for r in records if r.record_type == FILE_EVIDENCE]
    if files or any(i["type"] == SUMMARY_EVIDENCE for i in snapshot):
        number = int(episode.attributes["number"])
        records.append(
            mappers.files_summary_evidence(files, number, checked_at, expected_files=expected)
        )
    return records


_PATH_FACTS = ("path", "status", "additions", "deletions")


def _episode(
    report: Mapping[str, Any], dataset_agent: str, file_facts: Mapping[str, Any] | None
) -> Episode:
    ref = report["pull_request"]["ref"]
    url_repo, number = ref.lower().rsplit("#", 1)
    # The repository's current name, as the live run used it (renamed repositories still
    # answer at their old URL, and evidence about them carries the current name).
    repo = (
        (file_facts or {}).get("repo") or report["pull_request"].get("repo") or url_repo
    ).lower()
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
    """The stored claim time; for older records, rebuilt: merge claims at the merge, text claims
    at the PR's opening (a commit or comment claim was later; ``_reproduces`` guards against
    the difference)."""
    if item.get("made_at"):
        return _time(item["made_at"])
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
        "observed_at": record.observed_at.isoformat(),
        "version": record.version,
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
