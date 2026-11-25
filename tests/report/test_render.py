"""Tests for report rendering (report/render.py) using the shared fake agent PR."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from second_source.pipeline import check_pull_request
from second_source.report.render import (
    escape_markdown_cell,
    rows,
    to_json,
    to_markdown,
    to_text,
)
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.source import GitHubSource
from second_source.sources.github.urls import PullRequestRef

NOW = datetime(2026, 11, 21, tzinfo=UTC)


@pytest.fixture
def report(fake_github):
    client = GitHubClient(transport=httpx.MockTransport(fake_github()), sleep=lambda _s: None)
    return check_pull_request(PullRequestRef("Octo", "App", 7), GitHubSource(client), now=NOW)


def test_rows_put_problems_first(report):
    results = [row.result.value for row in rows(report)]
    assert results == sorted(
        results, key=["CONTRADICTED", "UNVERIFIABLE", "PENDING", "VERIFIED"].index
    )


def test_text_report_names_agent_claims_and_evidence(report):
    text = to_text(report)
    assert "Octo/App#7" in text
    assert "Agent: codex (detected by branch_prefix: codex/)" in text
    assert "✔ verified     Tests pass (low confidence)" in text
    assert 'said in body: "- ✅ `pytest -q`"' in text
    assert "\033[" not in text  # no colour codes unless asked


def test_text_report_can_use_colour(report):
    assert "\033[32m" in to_text(report, color=True)


def test_markdown_is_a_table_with_escaped_cells(report):
    markdown = to_markdown(report)
    assert "| | Claim | Result | Why | Said in |" in markdown
    # "✅ `pytest -q`" comes from a rule not yet proven on real PRs, so it is low confidence.
    assert "| ✔ | Tests pass (low confidence) | verified |" in markdown
    assert "| ✔ | Tests added | verified |" in markdown  # a trusted rule: no label
    assert "never as contradicted" in markdown


def test_markdown_escapes_pipes():
    assert escape_markdown_cell("a | b\nc") == "a \\| b c"


def test_json_is_serialisable_and_versioned(report):
    data = to_json(report)
    json.dumps(data)  # must not raise
    assert data["schema_version"] == "2"
    assert data["agent"] == {"name": "codex", "signal": "branch_prefix"}
    assert data["summary"]["verified"] >= 3
    tests_pass = next(c for c in data["claims"] if c["type"] == "TESTS_PASS")
    assert tests_pass["result"] == "VERIFIED"
    assert tests_pass["checks"][0]["id"] == "coding.ci_result"


def test_stale_data_is_called_out(fake_github):
    limited = httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    fake = fake_github({"/repos/Octo/App/commits/c1/check-runs": limited})
    client = GitHubClient(transport=httpx.MockTransport(fake), sleep=lambda _s: None)
    report = check_pull_request(PullRequestRef("Octo", "App", 7), GitHubSource(client), now=NOW)
    assert "Not fully checked" in to_text(report)
    assert "Not fully checked" in to_markdown(report)


def test_a_withdrawn_verification_says_why_in_words(fake_github):
    # The PR skips a test in the same change that claims "tests pass".
    files = [
        {
            "filename": "src/parser.py",
            "status": "modified",
            "additions": 4,
            "deletions": 1,
            "patch": "@@ -1,1 +1,2 @@\n x = 1\n+y = 2",
        },
        {
            "filename": "tests/test_parser.py",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": "@@ -1,1 +1,2 @@\n+@pytest.mark.skip\n def test_empty():",
        },
    ]
    pull = dict(fake_github.BASE_ROUTES["/repos/Octo/App/pulls/7"], body="All tests pass.")
    fake = fake_github({"/repos/Octo/App/pulls/7": pull, "/repos/Octo/App/pulls/7/files": files})
    client = GitHubClient(transport=httpx.MockTransport(fake), sleep=lambda _s: None)
    report = check_pull_request(PullRequestRef("Octo", "App", 7), GitHubSource(client), now=NOW)
    markdown = to_markdown(report)
    assert "| ⚠ | Tests pass | not verified | this PR skips tests |" in markdown
    claim = next(c for c in to_json(report)["claims"] if c["type"] == "TESTS_PASS")
    assert (claim["result"], claim["withdrawn"]) == ("UNVERIFIABLE", ["PR_SKIPS_TESTS"])


def test_a_claim_unverifiable_for_its_own_reasons_is_not_shown_as_withdrawn():
    from second_source.core.model import CheckKind, CheckRun, Level, Result, Verdict
    from second_source.report.render import withdrawn_reasons

    def check_run(check_id, result, reason, role="check"):
        return CheckRun(
            check_id, "1.0.0", CheckKind.STATE, Level.L4, "c", result, reason, "h", role=role
        )

    ci_missing = check_run("coding.ci_result", Result.UNVERIFIABLE, "NO_CI_RECORDS")
    skips = check_run("coding.integrity_guard", Result.UNVERIFIABLE, "PR_SKIPS_TESTS", "guard")
    both = Verdict(
        "c", Result.UNVERIFIABLE, ("NO_CI_RECORDS", "PR_SKIPS_TESTS"), (ci_missing, skips)
    )
    assert withdrawn_reasons(both) == ()
    passed = check_run("coding.ci_result", Result.VERIFIED, "ALL_MATCHING_CI_JOBS_PASSED")
    only_guard = Verdict("c", Result.UNVERIFIABLE, ("PR_SKIPS_TESTS",), (passed, skips))
    assert withdrawn_reasons(only_guard) == ("PR_SKIPS_TESTS",)


def test_watched_claims_say_what_is_being_watched(report):
    # Stage B: "Fixes #3: pending (IN_WINDOW)" read as if the issue were still open.
    markdown = to_markdown(report)
    assert "issue closed; watching for a reopen" in markdown
    assert "not reverted so far; watching for a revert" in markdown
    reasons = {c["type"]: c["reasons"] for c in to_json(report)["claims"]}
    assert reasons["FIXES_ISSUE"] == ["IN_WINDOW"]  # machine-readable codes are unchanged


def report_with_body(fake_github, body):
    pull = {**fake_github.BASE_ROUTES["/repos/Octo/App/pulls/7"], "body": body}
    fake = fake_github({"/repos/Octo/App/pulls/7": pull})
    client = GitHubClient(transport=httpx.MockTransport(fake), sleep=lambda _s: None)
    return check_pull_request(PullRequestRef("Octo", "App", 7), GitHubSource(client), now=NOW)


def test_pr_text_in_the_table_is_inert(fake_github):
    # Security review 1, M1: raw HTML in a quote forged a "✔ verified" row on GitHub.
    body = "All tests pass </td></tr><tr><td>✔</td><td>Fake</td> @org/team ` | [x](https://e.x)"
    markdown = to_markdown(report_with_body(fake_github, body))
    said = next(line for line in markdown.splitlines() if "Tests pass" in line)
    cell = said.rsplit("| body: ", 1)[1]
    assert cell.startswith("`` ") and cell.rstrip(" |").endswith(" ``")  # one code span
    assert "\\|" in cell  # the pipe cannot end the cell


def test_control_characters_never_reach_the_terminal(fake_github):
    # Security review 1, L2: an OSC 52 sequence would write the reader's clipboard.
    text = to_text(report_with_body(fake_github, "All tests pass \x1b]52;c;ZWNobw==\x07 ok"))
    assert "\x1b" not in text and "\x07" not in text


def test_a_repository_named_in_pr_text_is_never_a_live_link(fake_github):
    # Security review 2, N8: GitHub autolinks "www." names in plain table text.
    markdown = to_markdown(report_with_body(fake_github, "Fixes a/_www.evil-example.com#1"))
    row = next(line for line in markdown.splitlines() if "Fixes" in line and "#1" in line)
    assert "`` a/_www.evil-example.com#1 ``" in row or "` a/_www.evil-example.com#1 `" in row


def test_every_reason_a_check_can_give_has_words():
    # People read check runs; codes stay in the JSON. Guards' own "nothing found" reasons
    # never reach a reader (a guard that found nothing is not shown).
    import re as _re
    from pathlib import Path

    from second_source.report.render import GUARD_TEXT, REASON_TEXT

    checks = Path(__file__).resolve().parents[2] / "src/second_source/packs/coding/checks"
    shown = set()
    for path in checks.glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        guard = "role: guard" in text
        for result, reason in _re.findall(r"result: (\w+), reason: (\w+)", text) + [
            (r, c) for r, c in _re.findall(r"result: (\w+)\n\s+reason: (\w+)", text)
        ]:
            if not (guard and result == "VERIFIED"):
                shown.add(reason)
    assert shown - set(GUARD_TEXT) - set(REASON_TEXT) == set()
