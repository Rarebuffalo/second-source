"""PR text is attacker-controlled: extraction must stay fast and never crash on any of it.

Security review 1 found PR bodies that took minutes per pass (catastrophic backtracking, H2),
numbers that crashed the run (M4) and issue references that walked API paths (L1). Each
input below is one of those shapes at full size; every one must be handled well within a
time budget.
"""

import time
from datetime import UTC, datetime

import pytest

from second_source.action.commands import parse_command
from second_source.packs.coding import diff_facts
from second_source.packs.coding.extract import TextSource, extract_claims

T0 = datetime(2026, 11, 20, tzinfo=UTC)
BUDGET = 2.0  # seconds for a whole extraction; the slow patterns took minutes
SIZE = 65_000  # GitHub's PR body limit is 65,536 characters

ADVERSARIAL = {
    "spaces": " " * SIZE,
    "newlines": "\n" * SIZE,
    "unclosed html comments": "<!--" * (SIZE // 4),
    "unclosed bot sections": "<!-- This is an auto-generated comment " * (SIZE // 40),
    "unclosed fences": "```" * (SIZE // 3),
    "heading of spaces": "# a" + " " * SIZE + "x",
    "checkbox of spaces": " " * SIZE + "[",
    "clause breaks": ("tests pass" + " " * 5000 + "x") * 12,
    "quote markers": (" " * 100 + "\n") * (SIZE // 101) + ">",
    "count of spaces": "1" + " " * SIZE + "/",
    "fix of spaces": "fixes" + " " * SIZE + "x",
    "huge issue number": "Fixes #" + "1" * 5000,
    "huge count": "Tests pass: " + "9" * 5000 + "/" + "9" * 5000,
    "many references": " ".join(f"Fixes #{n}." for n in range(1, 6000)),
    # Security review 2 (N1, N5): what the first fixes missed.
    "marked code of spaces": "`a`"
    + " " * 2000
    + "\n"
    + ("`pytest`" + " " * 4000 + "passed\n") * 15,
    "clause break run": "x" + " " * 40_000 + "y",
}


@pytest.mark.parametrize("text", ADVERSARIAL.values(), ids=ADVERSARIAL.keys())
def test_extraction_is_fast_and_never_crashes(text):
    started = time.perf_counter()
    for origin in ("body", "commit"):
        extract_claims("o/r#1", [TextSource(origin, text, T0, ref="c1")])
    assert time.perf_counter() - started < BUDGET


def test_an_issue_number_too_long_for_github_is_not_a_claim():
    claims = extract_claims("o/r#1", [TextSource("body", "Fixes #" + "1" * 5000, T0)])
    assert claims == []


def test_issue_references_never_name_a_path():
    for text in ("Fixes ../..#2", "Fixes ./x#2", "Fixes a/..#2"):
        claims = extract_claims("o/r#1", [TextSource("body", text, T0)])
        assert all(".." not in c.attributes["repo"] for c in claims), text
        assert all(c.attributes["repo"] not in (".", "./x") for c in claims), text


def test_a_command_line_is_one_line_and_fast():
    command = parse_command("/second-source dispute\n@everyone click here", "a", "OWNER")
    assert command is not None and command.argument == ""
    started = time.perf_counter()
    parse_command("/second-source" + " " * SIZE + "x", "a", "OWNER")
    parse_command((" " * 1000 + "\n") * 60, "a", "OWNER")
    assert time.perf_counter() - started < BUDGET


def test_coverage_config_lines_are_fast():
    patch = "@@ -1,1 +1,2 @@\n+fail_under" + " " * 1900 + "x\n" * 30
    started = time.perf_counter()
    diff_facts.facts("pyproject.toml", "modified", patch)
    assert time.perf_counter() - started < BUDGET


def test_skip_markers_in_long_lines_are_fast():
    # Security review 2, N5: 300 lines like this took 19 s in one call.
    line = "+@unittest.skip" + " " * 1980 + "!\n"
    patch = "@@ -1,1 +1,300 @@\n" + line * 300
    started = time.perf_counter()
    diff_facts.facts("tests/test_x.py", "modified", patch)
    assert time.perf_counter() - started < BUDGET


def test_case_folding_cannot_shift_what_is_removed():
    # Security review 2, N4: "İ" lower-cases to two characters, which shifted the cut and
    # turned fenced (quoted) text into a claim.
    text = "İ" * 50 + "\n```\nAll tests pass\n```\n"
    assert extract_claims("o/r#1", [TextSource("body", text, T0, ref="c1")]) == []


def test_a_bot_section_past_the_length_cap_is_still_the_bots():
    # Security review 2, N4: cutting before removing blocks credited a bot's words to the agent.
    bot = "<!-- This is an auto-generated comment: summary -->\nFixes #77.\n"
    bot += "<!-- end of auto-generated comment: summary -->"
    text = "Some notes. " * 2000 + bot
    claims = extract_claims("o/r#1", [TextSource("body", text, T0, ref="c1")])
    assert not any(c.attributes.get("issue") == 77 for c in claims)
