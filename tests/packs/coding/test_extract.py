"""Tests for rule-based claim extraction (packs/coding/extract.py).

Each case is small and named after the behaviour it protects. The larger labelled golden set
(measuring precision and recall) lives in tests/packs/coding/golden/.
"""

from datetime import UTC, datetime, timedelta

import pytest

from second_source.packs.coding.extract import (
    BUILD_OK,
    DEPLOYED,
    DOCS_UPDATED,
    FIXES_ISSUE,
    LINT_OK,
    MERGE_HOLDS,
    TESTS_ADDED,
    TESTS_PASS,
    TextSource,
    clauses,
    clean,
    extract_claims,
    is_assertion,
    merge_claim,
)

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


def body(text, *, ref="abc123", origin="body", at=T0):
    return TextSource(origin=origin, text=text, written_at=at, ref=ref)


def types(text):
    return sorted(claim.type for claim in extract_claims("pr-1", [body(text)]))


# ---- positive cases --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("All tests pass.", [TESTS_PASS]),
        ("- [x] Tests pass", [TESTS_PASS]),
        ("The test suite passes on all platforms.", [TESTS_PASS]),
        ("CI is green", [TESTS_PASS]),
        ("pytest now passes with 120 tests", [TESTS_PASS]),
        ("✅ Tests", [TESTS_PASS]),
        ("Build succeeds", [BUILD_OK]),
        ("The project compiles cleanly", [BUILD_OK]),
        ("Lint passes", [LINT_OK]),
        ("No lint errors", [LINT_OK]),
        ("Added unit tests for the parser", [TESTS_ADDED]),
        ("New tests covering the retry logic", [TESTS_ADDED]),
        ("Updated the README", [DOCS_UPDATED]),
        ("Fixes #123", [FIXES_ISSUE]),
        ("Deployed to staging", [DEPLOYED]),
        ("Added tests and all tests pass", [TESTS_ADDED, TESTS_PASS]),
        ("CI is green and the docs are updated", [TESTS_PASS, DOCS_UPDATED]),
        ("Added regression tests and they are all passing", [TESTS_ADDED, TESTS_PASS]),
    ],
)
def test_finds_claims(text, expected):
    assert types(text) == sorted(expected)


# ---- negative cases: not assertions, or not claims at all ------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "- [ ] Tests pass",  # unchecked checklist item is a to-do
        "Tests should pass once CI runs",  # expectation, not a claim
        "I will add tests later",  # plan
        "Tests are not passing yet",  # negation
        "Tests don't pass on Windows",  # contracted negation
        "Do the tests pass?",  # question
        "Some tests are failing",  # failure report
        "Partially fixes #12",  # hedge
        "Related to #12",  # not a closing keyword
        "> All tests pass",  # quoted text is someone else's words
        "```\nall tests pass\n```",  # code block
        "Run `npm test` to check",  # inline code only
        "<!-- all tests pass -->",  # HTML comment
        "Refactor the attestation module",  # 'test' inside another word
        "Adds a retry loop to the client",  # no claim
    ],
)
def test_ignores_non_claims(text):
    assert types(text) == []


# ---- command status lines (e.g. Codex "Testing" sections) ------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("## Testing\n- ✅ `pytest -q`", [TESTS_PASS]),
        (
            "- ✅ `npm test`\n- ✅ `npm run lint`\n- ✅ `npm run build`",
            [BUILD_OK, LINT_OK, TESTS_PASS],
        ),
        ("- `cargo test` ✔", [TESTS_PASS]),
        ("- ❌ `pytest` (fails: missing fixture)", []),  # failure report
        ("- ⚠️ `npm test` *(could not run: no network)*", []),  # warning, not success
        ("- ✅ `pytest` (not run: missing dependency)", []),  # prose says it did not run
        ("- ✅ `git status`", []),  # not a test, lint or build command
        ("```\n- ✅ `pytest`\n```", []),  # inside a code block
    ],
)
def test_command_status_lines(text, expected):
    assert types(text) == sorted(expected)


def test_command_claims_merge_with_prose_claims_at_the_same_commit():
    # "All tests pass" and "✅ `pytest`" at the same commit are one claim, not two.
    assert types("All tests pass.\n- ✅ `pytest -q`") == [TESTS_PASS]


# ---- attributes ------------------------------------------------------------------------------


def test_ref_is_attached_to_commit_dependent_claims():
    (claim,) = extract_claims("pr-1", [body("All tests pass", ref="deadbeef")])
    assert claim.attributes["ref"] == "deadbeef"


def test_local_qualifier_is_recorded():
    (claim,) = extract_claims("pr-1", [body("All tests pass locally")])
    assert claim.attributes["qualifier"] == "locally"


def test_issue_references_capture_number_and_repo():
    claims = extract_claims("pr-1", [body("Fixes #12. Closes octo/repo#34")])
    issues = sorted((c.attributes["repo"], c.attributes["issue"]) for c in claims)
    assert issues == [("", 12), ("octo/repo", 34)]


def test_deploy_environment_is_normalised():
    (claim,) = extract_claims("pr-1", [body("Deployed to prod")])
    assert claim.attributes["environment"] == "production"


def test_span_and_origin_are_kept():
    (claim,) = extract_claims("pr-1", [body("Summary:\n- [x] All tests pass", origin="comment")])
    assert claim.origin == "comment"
    assert claim.span == "All tests pass"


# ---- de-duplication and stability ------------------------------------------------------------


def test_same_claim_at_same_commit_is_kept_once_from_the_earliest_source():
    later = body("All tests pass", origin="comment", at=T0 + timedelta(hours=1))
    earlier = body("Tests pass", origin="body", at=T0)
    (claim,) = extract_claims("pr-1", [later, earlier])
    assert claim.origin == "body"


def test_same_claim_at_different_commits_is_kept_twice():
    first = body("All tests pass", ref="aaa")
    second = body("All tests pass", ref="bbb", at=T0 + timedelta(hours=1))
    assert len(extract_claims("pr-1", [first, second])) == 2


def test_claim_ids_are_deterministic():
    sources = [body("Fixes #3. All tests pass.")]
    first = [c.id for c in extract_claims("pr-1", sources)]
    assert first == [c.id for c in extract_claims("pr-1", sources)]
    assert first != [c.id for c in extract_claims("pr-2", sources)]


# ---- helpers ---------------------------------------------------------------------------------


def test_clean_removes_code_comments_and_quotes():
    text = "keep\n```\ndrop\n```\n<!-- drop -->\n> drop\nkeep `drop` too"
    assert "drop" not in clean(text)
    assert clean(text).count("keep") == 2


def test_clauses_split_lines_sentences_and_checklists():
    text = "First sentence. Second one!\n- [x] Checked item\n- [ ] Unchecked item\n* bullet"
    assert list(clauses(text)) == ["First sentence.", "Second one!", "Checked item", "bullet"]


def test_is_assertion():
    assert is_assertion("All tests pass")
    assert not is_assertion("Will this pass?")


def test_merge_claim_is_implicit_and_deterministic():
    claim = merge_claim("pr-1", T0, "cafe")
    assert claim.type == MERGE_HOLDS
    assert claim.attributes == {"merge_sha": "cafe"}
    assert claim.id == merge_claim("pr-1", T0, "cafe").id
