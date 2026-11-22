"""Tests for offline re-scoring (benchmark/rescore.py): replay verdicts from evidence snapshots."""

import copy
import json
from datetime import UTC, datetime

import httpx

from second_source.benchmark.aidev import AidevPullRequest
from second_source.benchmark.rescore import (
    fetch_file_facts,
    needs_file_facts,
    rescore,
    rescore_all,
)
from second_source.benchmark.runner import read_results, run_benchmark
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.source import GitHubSource

NOW = datetime(2026, 11, 21, tzinfo=UTC)
URL = "https://github.com/Octo/App/pull/7"
SKIP_PATCH = "@@ -1,2 +1,4 @@\n import pytest\n+@pytest.mark.skip\n def test_parse():\n+    pass"


def client(fake):
    return GitHubClient(transport=httpx.MockTransport(fake), sleep=lambda _s: None)


def checked_record(fake, tmp_path):
    out = tmp_path / "results.jsonl"
    row = AidevPullRequest(URL, "codex", "t", "b", "closed", None, "2026-11-20T12:00:00Z")
    run_benchmark([row], out, source=GitHubSource(client(fake)), clock=lambda: NOW)
    (record,) = read_results(out)
    return record


def as_old_record(record):
    """What a record from before the integrity guards looks like: no diff counts, no summary,
    no code versions."""
    old = copy.deepcopy(record)
    old.pop("tool_version")
    old.pop("pack_version")
    old["evidence"] = [e for e in old["evidence"] if e["type"] != "changed_files_summary"]
    for item in old["evidence"]:
        if item["type"] == "changed_file":
            item["attributes"] = {
                k: item["attributes"][k] for k in ("path", "status", "additions", "deletions")
            }
    return old


def claim(record, claim_type):
    return next(c for c in record["report"]["claims"] if c["type"] == claim_type)


def test_rescoring_with_the_same_code_changes_no_result(fake_github, tmp_path):
    record = checked_record(fake_github(), tmp_path)
    again = rescore(record, pack_version="9.9.9")
    assert [c["result"] for c in again["report"]["claims"]] == [
        c["result"] for c in record["report"]["claims"]
    ]
    assert [c["reasons"] for c in again["report"]["claims"]] == [
        c["reasons"] for c in record["report"]["claims"]
    ]
    assert not any("rescore_mismatch" in c for c in again["report"]["claims"])
    assert again["pack_version"] == "9.9.9"
    assert again["rescored_from"]["pack_version"] == record["pack_version"]
    json.dumps(again)  # still JSON-safe


def test_an_old_record_without_diff_counts_is_shown_as_not_inspected(fake_github, tmp_path):
    old = as_old_record(checked_record(fake_github(), tmp_path))
    assert needs_file_facts(old)
    again = rescore(old, pack_version="x")
    tests_pass = claim(again, "TESTS_PASS")
    assert tests_pass["result"] == "VERIFIED"  # never withdrawn for lack of a diff
    assert "NOT_INSPECTED" in tests_pass["caveats"]


def test_a_new_record_needs_no_facts(fake_github, tmp_path):
    assert not needs_file_facts(checked_record(fake_github(), tmp_path))


def test_re_read_diff_counts_let_the_guards_withdraw(fake_github, tmp_path):
    old = as_old_record(checked_record(fake_github(), tmp_path))
    files = copy.deepcopy(fake_github.BASE_ROUTES["/repos/Octo/App/pulls/7/files"])
    files[1]["patch"] = SKIP_PATCH  # tests/test_parser.py now adds a skip marker
    fake = fake_github({"/repos/Octo/App/pulls/7/files": files})
    facts = fetch_file_facts(client(fake), URL, clock=lambda: NOW)
    assert '"patch":' not in json.dumps(facts)  # counts only, never code
    again = rescore(old, file_facts=facts, pack_version="x")
    tests_pass = claim(again, "TESTS_PASS")
    assert tests_pass["result"] == "UNVERIFIABLE"
    assert tests_pass["withdrawn"] == ["PR_SKIPS_TESTS"]
    assert not any("rescore_mismatch" in c for c in again["report"]["claims"])


def test_a_disagreement_keeps_the_stored_result_and_is_counted(fake_github, tmp_path):
    record = checked_record(fake_github(), tmp_path)
    assert claim(record, "TESTS_ADDED")["result"] == "VERIFIED"
    # The test file is gone from the snapshot, so a re-score would decide differently; that is
    # not a guard withdrawal, so the stored verdict stands and is flagged.
    record["evidence"] = [
        e for e in record["evidence"] if e["attributes"].get("path") != "tests/test_parser.py"
    ]
    (again,), mismatches = rescore_all([record], {}, pack_version="x")
    added = claim(again, "TESTS_ADDED")
    assert added["result"] == "VERIFIED" and added["rescore_mismatch"] != "VERIFIED"
    assert mismatches >= 1


def test_error_records_pass_through(tmp_path):
    record = {"url": URL, "error": "not_found"}
    assert rescore(record, pack_version="x") == record
    assert not needs_file_facts(record)
