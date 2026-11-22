"""Tests for offline re-scoring (benchmark/rescore.py): replay verdicts from evidence snapshots."""

import copy
import json
from datetime import UTC, datetime

import httpx
import pytest

from second_source.benchmark.aidev import AidevPullRequest
from second_source.benchmark.rescore import (
    AlreadyRescored,
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
    for item in old["report"]["claims"]:  # the guards did not exist yet
        item["checks"] = [run for run in item["checks"] if run["role"] != "guard"]
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


def test_a_disagreement_becomes_unverifiable_and_is_counted(fake_github, tmp_path):
    record = checked_record(fake_github(), tmp_path)
    assert claim(record, "TESTS_ADDED")["result"] == "VERIFIED"
    # The test file is gone from the snapshot, so an unchanged check decides differently:
    # neither run can be trusted, so the claim is unverifiable and flagged.
    record["evidence"] = [
        e for e in record["evidence"] if e["attributes"].get("path") != "tests/test_parser.py"
    ]
    (again,), mismatches = rescore_all([record], {}, pack_version="x")
    added = claim(again, "TESTS_ADDED")
    assert (added["result"], added["reasons"]) == ("UNVERIFIABLE", ["RESCORE_MISMATCH"])
    assert added["stored_result"] == "VERIFIED" and added["rescore_mismatch"] != "VERIFIED"
    assert mismatches >= 1


def test_error_records_pass_through(tmp_path):
    record = {"url": URL, "error": "not_found"}
    assert rescore(record, pack_version="x") == record
    assert not needs_file_facts(record)


def test_a_check_with_a_new_version_may_decide_differently(fake_github, tmp_path):
    record = checked_record(fake_github(), tmp_path)
    added = claim(record, "TESTS_ADDED")
    # Pretend the stored verdict came from an older version of the check that contradicted.
    added["result"] = "CONTRADICTED"
    for run in added["checks"]:
        if run["id"] == "coding.tests_added":
            run.update(version="0.9.0", result="CONTRADICTED")
    again = claim(rescore(record, pack_version="x"), "TESTS_ADDED")
    assert again["result"] == "VERIFIED" and "rescore_mismatch" not in again


# ---- review findings (private/research/rescore-review.md) --------------------------------------


def test_new_records_keep_claim_times_versions_and_fetched_types(fake_github, tmp_path):
    record = checked_record(fake_github(), tmp_path)
    assert all(c["made_at"] for c in record["report"]["claims"])
    assert all("version" in e and "observed_at" in e for e in record["evidence"])
    assert "changed_file" in record["report"]["fetched"]
    assert record["report"]["pull_request"]["repo"] == "octo/app"


def test_diff_counts_from_other_code_are_dropped_not_trusted(fake_github, tmp_path):
    files = copy.deepcopy(fake_github.BASE_ROUTES["/repos/Octo/App/pulls/7/files"])
    files[1]["patch"] = SKIP_PATCH
    record = checked_record(fake_github({"/repos/Octo/App/pulls/7/files": files}), tmp_path)
    assert claim(record, "TESTS_PASS")["withdrawn"] == ["PR_SKIPS_TESTS"]
    for item in record["evidence"]:
        if item["type"] == "changed_files_summary":
            item["attributes"]["facts_version"] = "older-code"
    assert needs_file_facts(record)
    again = claim(rescore(record, pack_version="x"), "TESTS_PASS")
    # Without a re-read, the stale counts say nothing: not inspected, not withdrawn, not clean.
    assert again["result"] == "VERIFIED" and "NOT_INSPECTED" in again["caveats"]


def test_facts_made_by_other_code_are_ignored(fake_github, tmp_path):
    old = as_old_record(checked_record(fake_github(), tmp_path))
    facts = fetch_file_facts(client(fake_github()), URL, clock=lambda: NOW)
    facts["facts_version"] = "older-code"
    again = claim(rescore(old, file_facts=facts, pack_version="x"), "TESTS_PASS")
    assert "NOT_INSPECTED" in again["caveats"]


def test_facts_carry_the_repositorys_current_name(fake_github, tmp_path):
    routes = fake_github().routes
    pull = routes["/repos/Octo/App/pulls/7"]
    base = {**pull["base"], "repo": {**pull["base"]["repo"], "full_name": "Octo/NewApp"}}
    facts = fetch_file_facts(
        client(fake_github({"/repos/Octo/App/pulls/7": {**pull, "base": base}})), URL
    )
    assert facts["repo"] == "Octo/NewApp" and isinstance(facts["changed_files"], int | None)
    # An old record of a renamed repository: its issue record carries the current name.
    record = as_old_record(checked_record(fake_github(), tmp_path))
    record["report"]["pull_request"].pop("repo")
    for item in record["evidence"]:
        if item["type"] == "issue_state":
            item["attributes"]["repo"] = "octo/newapp"
    without = claim(rescore(record, pack_version="x"), "FIXES_ISSUE")
    assert without["result"] == "UNVERIFIABLE"  # the URL name cannot match the issue record
    with_facts = claim(rescore(record, file_facts=facts, pack_version="x"), "FIXES_ISSUE")
    assert with_facts["result"] == claim(record, "FIXES_ISSUE")["result"]


def test_a_type_the_original_run_never_fetched_is_stale(fake_github, tmp_path):
    record = checked_record(fake_github(), tmp_path)
    record["report"]["fetched"] = [t for t in record["report"]["fetched"] if t != "revert"]
    record["evidence"] = [e for e in record["evidence"] if e["type"] != "revert"]
    merge = claim(rescore(record, pack_version="x"), "MERGE_HOLDS")
    assert merge["result"] != "VERIFIED"


def test_a_record_that_cannot_be_rebuilt_is_unverifiable_not_a_crash(fake_github, tmp_path):
    record = checked_record(fake_github(), tmp_path)
    del record["report"]["pull_request"]["opened_at"]
    (again,), mismatches = rescore_all([record], {}, pack_version="x")
    assert "rescore_error" in again
    assert {c["result"] for c in again["report"]["claims"]} == {"UNVERIFIABLE"}
    assert mismatches == len(again["report"]["claims"])


def test_rescoring_a_rescored_record_is_refused(fake_github, tmp_path):
    once = rescore(checked_record(fake_github(), tmp_path), pack_version="x")
    with pytest.raises(AlreadyRescored):
        rescore(once, pack_version="x")


def test_a_different_reason_at_the_same_version_is_a_mismatch(fake_github, tmp_path):
    record = checked_record(fake_github(), tmp_path)
    added = claim(record, "TESTS_ADDED")
    for run in added["checks"]:
        if run["id"] == "coding.tests_added":
            run["reason"] = "SOMETHING_ELSE"
    again = claim(rescore(record, pack_version="x"), "TESTS_ADDED")
    assert again["reasons"] == ["RESCORE_MISMATCH"]
