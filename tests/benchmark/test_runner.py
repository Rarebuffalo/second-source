"""Tests for the resumable batch runner (benchmark/runner.py) against the shared fake GitHub."""

import json
from datetime import UTC, datetime

import httpx

from second_source.benchmark.aidev import AidevPullRequest
from second_source.benchmark.runner import completed_urls, read_results, run_benchmark
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.source import GitHubSource

NOW = datetime(2026, 11, 21, tzinfo=UTC)
GOOD = "https://github.com/Octo/App/pull/7"
MISSING = "https://github.com/Octo/App/pull/99"


def pull(url, agent="codex"):
    return AidevPullRequest(url, agent, "t", "b", "closed", None, "2026-11-20T12:00:00Z")


def run(fake, rows, out, **kwargs):
    client = GitHubClient(transport=httpx.MockTransport(fake), sleep=lambda _s: None)
    return run_benchmark(rows, out, source=GitHubSource(client), clock=lambda: NOW, **kwargs)


def test_records_reports_and_errors(fake_github, tmp_path):
    out = tmp_path / "results.jsonl"
    messages = []
    summary = run(
        fake_github(), [pull(GOOD), pull(MISSING), pull("not a url")], out, progress=messages.append
    )
    assert (summary.checked, summary.errors, summary.stopped) == (1, 2, None)
    records = {r["url"]: r for r in read_results(out)}
    assert records[GOOD]["dataset_agent"] == "codex"
    assert records[GOOD]["report"]["agent"]["name"] == "codex"
    assert records[MISSING]["error"] == "not_found"
    assert records["not a url"]["error"].startswith("invalid url")
    assert len(messages) == 3


def test_resumes_without_rechecking(fake_github, tmp_path):
    out = tmp_path / "results.jsonl"
    run(fake_github(), [pull(GOOD)], out)
    summary = run(fake_github(), [pull(GOOD), pull(MISSING)], out)
    assert (summary.skipped, summary.errors, summary.checked) == (1, 1, 0)
    assert len(read_results(out)) == 2
    assert completed_urls(out) == {GOOD, MISSING}


def test_rate_limit_stops_without_recording_the_pr(fake_github, tmp_path):
    limited = httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    out = tmp_path / "results.jsonl"
    # Limit hit while fetching evidence: the report would be partial, so it is not recorded.
    fake = fake_github({"/repos/Octo/App/commits/c1/check-runs": limited})
    summary = run(fake, [pull(GOOD), pull(MISSING)], out)
    assert summary.stopped == "GitHub rate limit reached"
    assert completed_urls(out) == set()


def test_rate_limit_while_loading_the_pr_also_stops(fake_github, tmp_path):
    limited = httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    out = tmp_path / "results.jsonl"
    summary = run(fake_github({"/repos/Octo/App/pulls/7": limited}), [pull(GOOD)], out)
    assert summary.stopped == "GitHub rate limit reached"
    assert not out.read_text()


def test_limit_caps_new_checks_only(fake_github, tmp_path):
    out = tmp_path / "results.jsonl"
    run(fake_github(), [pull(GOOD)], out)
    summary = run(fake_github(), [pull(GOOD), pull(MISSING), pull("x")], out, limit=1)
    assert summary.skipped == 1 and summary.errors == 1
    assert summary.stopped == "reached the limit of 1 pull requests"


def test_completed_urls_of_a_missing_file_is_empty(tmp_path):
    assert completed_urls(tmp_path / "none.jsonl") == set()


def test_unexpected_errors_are_retried_and_replaced_by_the_later_result(fake_github, tmp_path):
    out = tmp_path / "results.jsonl"
    # First run: GitHub answers with something unexpected (as with renamed repositories before
    # the client followed redirects), so the PR gets an error record that is not final.
    broken = fake_github({"/repos/Octo/App/pulls/7": httpx.Response(418)})
    assert run(broken, [pull(GOOD)], out).errors == 1
    assert completed_urls(out) == set()

    summary = run(fake_github(), [pull(GOOD)], out)
    assert (summary.checked, summary.skipped) == (1, 0)
    records = read_results(out)
    assert len(records) == 1 and "report" in records[0]  # the newer record wins
    assert completed_urls(out) == {GOOD}


def test_permanent_errors_are_not_retried(fake_github, tmp_path):
    out = tmp_path / "results.jsonl"
    run(fake_github(), [pull(MISSING), pull("not a url")], out)
    summary = run(fake_github(), [pull(MISSING), pull("not a url")], out)
    assert (summary.skipped, summary.checked, summary.errors) == (2, 0, 0)


def test_results_keep_an_evidence_snapshot(fake_github, tmp_path):
    out = tmp_path / "results.jsonl"
    run(fake_github(), [pull(GOOD)], out)
    (record,) = read_results(out)
    assert record["schema_version"] == "2"
    by_type = {}
    for item in record["evidence"]:
        by_type.setdefault(item["type"], []).append(item)
    jobs = {item["attributes"]["name"] for item in by_type["check_result"]}
    assert {"pytest", "ruff"} <= jobs
    paths = {item["attributes"]["path"] for item in by_type["changed_file"]}
    assert "tests/test_parser.py" in paths
    json.dumps(record)  # everything is JSON-safe (datetimes as ISO strings)
