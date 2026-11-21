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
    command_scope,
    extract_claims,
    is_assertion,
    is_local,
    merge_claim,
    rule_hits,
    rule_names,
    trusted_rules,
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
        ("Compiles cleanly with no warnings", [BUILD_OK]),  # "no" in a positive phrase
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
        # Review findings: negations with "no"/"without", quantifiers and exceptions.
        "No tests were added in this PR",
        "No docs updated",
        "Merged without tests passing",
        "Most tests pass; one integration test is pending a fixture",
        "Some tests pass",
        "Tests pass except the e2e suite",
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


def test_check_mark_before_tests_added_is_not_a_passing_claim():
    # Review finding: "✅ Tests added" means tests were added, not that they pass.
    assert types("✅ Tests added for the parser") == [TESTS_ADDED]
    assert types("✅ Tests") == [TESTS_PASS]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- ✅ Created `tests/test_parser.py`", [TESTS_ADDED]),  # a new test file, not a run
        ("- ✅ Renamed `test_utils` to `helpers`", []),  # identifiers, not commands
    ],
)
def test_inline_code_that_is_not_a_command_is_not_a_test_run(text, expected):
    # Review finding: only command lines (pytest, npm, cargo, ...) count as test runs.
    assert types(text) == expected


def test_command_claims_merge_with_prose_claims_at_the_same_commit():
    # "All tests pass" and "✅ `pytest`" at the same commit are one claim, not two.
    assert types("All tests pass.\n- ✅ `pytest -q`") == [TESTS_PASS]


# ---- attributes ------------------------------------------------------------------------------


def test_ref_is_attached_to_commit_dependent_claims():
    (claim,) = extract_claims("pr-1", [body("All tests pass", ref="deadbeef")])
    assert claim.attributes["ref"] == "deadbeef"


def test_local_scope_is_recorded_from_the_clause():
    (claim,) = extract_claims("pr-1", [body("All tests pass locally")])
    assert claim.attributes["scope"] == "local"


def test_local_scope_is_recorded_from_the_section_heading():
    text = "## Local verification\n- All tests pass\n\n## Notes\n- Build succeeds"
    claims = {c.type: c for c in extract_claims("pr-1", [body(text)])}
    assert claims[TESTS_PASS].attributes["scope"] == "local"
    assert "scope" not in claims[BUILD_OK].attributes  # a later section resets the heading


def test_local_scope_applies_to_command_status_lines():
    text = "**Testing (in the sandbox):**\n- ✅ `pytest -q`"
    (claim,) = extract_claims("pr-1", [body(text)])
    assert claim.attributes["scope"] == "local"


@pytest.mark.parametrize(
    "text",
    [
        "Targeted tests passing.",
        "Unit tests for v3-importer-commons (19 tests) all passing.",
        "The 12 new tests pass.",
        "Unit tests pass.",
    ],
)
def test_claims_about_part_of_the_tests_have_subset_scope(text):
    (claim,) = [c for c in extract_claims("pr-1", [body(text)]) if c.type == TESTS_PASS]
    assert claim.attributes["scope"] == "subset"


@pytest.mark.parametrize(
    ("command", "scope"),
    [
        ("pytest tests/test_parser.py", "subset"),
        ("pytest -k parser", "subset"),
        ("pnpm test --filter web", "subset"),
        ("biome lint", "subset"),  # one named linter
        ("pytest -q", None),
        ("npm test", None),
    ],
)
def test_command_scope(command, scope):
    assert command_scope(command) == scope


def test_a_local_command_stays_local():
    text = "**Local checks:**\n- ✅ `pytest tests/test_parser.py`"
    (claim,) = extract_claims("pr-1", [body(text)])
    assert claim.attributes["scope"] == "local"


def test_unscoped_claims_have_no_scope():
    (claim,) = extract_claims("pr-1", [body("All tests pass")])
    assert "scope" not in claim.attributes


def test_scope_is_only_set_on_check_claims():
    (claim,) = extract_claims("pr-1", [body("Added unit tests locally")])
    assert claim.type == TESTS_ADDED and "scope" not in claim.attributes


@pytest.mark.parametrize(
    "text",
    ["ran it on my machine", "Local test run", "in the sandbox", "tests run locally"],
)
def test_is_local_phrases(text):
    assert is_local(text)


@pytest.mark.parametrize("text", ["a local variable", "localisation fixes", "the build"])
def test_is_local_ignores_unrelated_uses(text):
    assert not is_local(text)


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


def test_scope_does_not_create_a_duplicate_claim():
    # Review finding: "tests pass" and "tests pass locally" at one commit are one claim.
    claims = extract_claims("pr-1", [body("All tests pass. Tests pass locally.")])
    assert [c.type for c in claims] == [TESTS_PASS]


def test_own_repo_issue_reference_is_the_same_claim_as_a_plain_reference():
    claims = extract_claims("pr-1", [body("Fixes #12. Fixes Octo/App#12.")], repo="Octo/App")
    assert [(c.type, c.attributes["repo"]) for c in claims] == [(FIXES_ISSUE, "")]


def test_other_repo_issue_reference_stays_separate():
    claims = extract_claims("pr-1", [body("Fixes #12. Fixes octo/lib#12.")], repo="octo/app")
    assert sorted(c.attributes["repo"] for c in claims) == ["", "octo/lib"]


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


# ---- sentences with inline code (real agent phrasings) -------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ran `pnpm tsc --noEmit` and it completed successfully.", [LINT_OK]),
        ("- `pytest tests/test_api.py`: 201 passed", [TESTS_PASS]),
        ("- `cargo build` ✅", [BUILD_OK]),
        ("Ran `pnpm build` which failed due to a network error.", []),
        # A ✅ used as a section emoji is not a result for the commands after it.
        ("- **✅ Verification:** ran `npm test` to make sure nothing broke", []),
        # Purpose clauses say why a command ran, not what happened.
        ("Ran `npm test` to ensure the build succeeds", []),
    ],
)
def test_command_results_in_prose(text, expected):
    assert types(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- Created `tests/test_cache.py` to cover expiry", [TESTS_ADDED]),
        ("- **`docs/setup.md`**: Updated the install steps", [DOCS_UPDATED]),
        ("Added a section to `frame/rws/README.md`", [DOCS_UPDATED]),
        ("- Fixed `tests/test_cache.py` imports", []),  # editing a test file is not new tests
        ("Implemented the API as described in `docs/api.md`", []),  # a reference, not a change
        ("### New files\n- `tests/unit/CacheTests.cs` - unit tests", [TESTS_ADDED]),
        ("- [ ] Update `README.md`", []),  # unchecked to-do
    ],
)
def test_changed_files_named_in_inline_code(text, expected):
    assert types(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Unit tests pass but integration tests had authentication issues.", [TESTS_PASS]),
        ("Verified that tests pass and no other usages exist.", [TESTS_PASS]),
        ("All tests passed without any failures.", [TESTS_PASS]),
        ('All tests pass; narrowed the "not re-exported" note.', [TESTS_PASS]),
        # Plain "and" keeps a statement together, and a negation still applies to it.
        ("Tests and lint did not pass.", []),
        ("The build fails but tests pass.", [TESTS_PASS]),
    ],
)
def test_negations_only_cancel_their_own_part_of_a_sentence(text, expected):
    assert types(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("All new and existing unit tests for the filters plugin passed.", [TESTS_PASS]),
        ("All 28 existing RWS tests continue passing.", [TESTS_PASS]),
        ("7 proxy integration tests added (all passing)", [TESTS_ADDED, TESTS_PASS]),
        ("Local self-test passed.", []),  # a script testing itself, not the project's tests
        ("Repo checks pass.", []),  # too vague to say which checks
        ("All CI checks are green.", [TESTS_PASS]),
        ("Docs formatting, markdown lint and link audit pass.", [LINT_OK]),
        ("Changes have been verified with lint checks.", [LINT_OK]),
    ],
)
def test_real_agent_phrasings_for_checks(text, expected):
    assert sorted(types(text)) == sorted(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Added 3 new unit tests in `filters.spec.js`.", [TESTS_ADDED]),
        ("Added comprehensive unit tests covering token estimation.", [TESTS_ADDED]),
        ("11 tests covering all include patterns", [TESTS_ADDED]),
        ("Updated test assertions to verify the new command format.", [TESTS_ADDED]),
        ("Added support for tests in the plugin loader.", []),  # a feature, not new tests
        ("Updates the source-clickhouse user documentation.", [DOCS_UPDATED]),
        ("Updated the docs for the CLI.", [DOCS_UPDATED]),
        ("Added logging to the documentation builder.", []),  # docs tooling, not docs
        ("This PR fixes issue #2351 for long names.", [FIXES_ISSUE]),
        ("Fixes bug #11832.", [FIXES_ISSUE]),
        ("1. **New Files**:\n   - `tests/Cache/DiskCacheTests.cs` - Unit tests", [TESTS_ADDED]),
    ],
)
def test_real_agent_phrasings_for_changes(text, expected):
    assert types(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This PR fixes bug #12, where the plugin would misapply filters.", [FIXES_ISSUE]),
        ("Update the Cursor Cloud instructions in AGENTS.md", [DOCS_UPDATED]),
        ("Implemented the parser as specified in SPEC.md", []),  # no edit verb
    ],
)
def test_relative_clauses_and_plain_doc_file_names(text, expected):
    assert types(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Check lists and counts.
        ("- [x] Manual testing completed (depcheck, lint, compile all pass)", [LINT_OK]),
        ("- [x] `dotnet test -c Release` — 211 pass, 0 fail", [TESTS_PASS]),
        ("- [x] `dotnet build` — zero warnings", [BUILD_OK]),
        ("Verified compilation and tests for all affected crates.", [BUILD_OK, TESTS_PASS]),
        ("TypeScript checks pass.", [LINT_OK]),
        ("19/22 tests passing in the local environment", []),  # a partial count is a hedge
        # New tests and docs.
        ("Significantly improved test coverage.", [TESTS_ADDED]),
        ("Added rawEnergy assertions to existing tests.", [TESTS_ADDED]),
        ("Add test metadata for better filtering.", []),
        ("Created test scripts to try the feature by hand.", []),
        ("I've included tests if applicable.", []),  # conditional checklist item
        ("- update progress in TypeScript README", [DOCS_UPDATED]),
        ("- new README and TASKS for the C transpiler", [DOCS_UPDATED]),
        ("README.md updated with the new flag.", [DOCS_UPDATED]),
        ("Fixed the docs build pipeline.", []),
    ],
)
def test_second_round_real_phrasings(text, expected):
    assert sorted(types(text)) == sorted(expected)


def test_review_bot_sections_are_ignored():
    text = (
        "Small fix.\n<!-- This is an auto-generated comment: release notes by coderabbit.ai -->\n"
        "* **Tests**\n  * Added tests for edge cases.\n"
        "<!-- end of auto-generated comment: release notes by coderabbit.ai -->"
    )
    assert types(text) == []


# ---- rule names (for per-rule precision) ---------------------------------------------------


def test_claims_record_the_rule_that_found_them():
    claims = extract_claims("pr-1", [body("All tests pass.\nFixes #3\n- ✅ `ruff check .`")])
    rules = {c.type: c.attributes["rule"] for c in claims}
    assert rules == {
        TESTS_PASS: "tests_pass.plain",
        FIXES_ISSUE: "fixes_issue.closing_ref",
        LINT_OK: "command.marked",
    }


def test_rule_hits_reports_every_matching_rule():
    hits = rule_hits("All new tests pass.")
    assert {("TESTS_PASS", "tests_pass.plain"), ("TESTS_PASS", "tests_pass.quantified")} <= hits


def test_rule_names_are_unique():
    names = rule_names()
    assert len(names) == len(set(names)) == 40  # 34 clause rules + issue, deploy, 4 code-sentence


# ---- confidence from trusted rules ---------------------------------------------------------


def test_claims_from_trusted_rules_are_high_confidence():
    (claim,) = extract_claims("pr-1", [body("Tests pass.")])
    assert claim.attributes["confidence"] == "high"


def test_claims_from_unproven_rules_are_low_confidence():
    (claim,) = extract_claims("pr-1", [body("The build succeeds.")])
    assert claim.attributes["confidence"] == "low"


@pytest.mark.parametrize("origin", ["commit", "comment", "title"])
def test_trusted_rules_give_low_confidence_outside_pr_bodies(origin):
    # Rule precision was measured on PR bodies; commit messages and comments are unmeasured.
    (claim,) = extract_claims("pr-1", [body("Tests pass.", origin=origin)])
    assert claim.attributes["confidence"] == "low"


def test_a_commit_claim_repeated_in_the_body_becomes_high_confidence():
    commit = body("Tests pass.", origin="commit", at=T0)
    pr_body = body("Tests pass.", at=T0 + timedelta(minutes=5))
    (claim,) = extract_claims("pr-1", [commit, pr_body])
    assert claim.origin == "commit"  # the earliest statement is kept
    assert claim.attributes["confidence"] == "high"


def test_a_repeated_claim_is_upgraded_when_a_trusted_rule_finds_it():
    text = "Verified with the existing unit tests.\n\nAll tests pass."
    (claim,) = extract_claims("pr-1", [body(text)])
    assert claim.span == "Verified with the existing unit tests."  # earliest statement kept
    assert (claim.attributes["rule"], claim.attributes["confidence"]) == (
        "tests_pass.plain",
        "high",
    )


def test_trusted_rules_file_names_real_rules():
    known = set(rule_names())
    assert trusted_rules()
    assert all(rule in known for _kind, rule in trusted_rules())


# ---- false claims seen in benchmark run 1 -------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # A template rule ticked as a checkbox is a condition, not a statement.
        "- [x] If this pull request adds new functionality, it includes unit tests",
        "Tests pass if you run them with the dev profile.",
        # Features and infrastructure named after testing.
        "Added save and test connection functionality",
        "Created test PR #69792 to check the hook",
        "Add conditional integration test workflow",
        # A relative clause about another change.
        "Follows up on #12, which added schema munging tests.",
        # Inline code docs and other nouns are not documentation files.
        "Add XML doc comments to the public API",
        "Add doc info and detailed parse errors",
        "Add dev and docs extras to the optional dependencies",
    ],
)
def test_phrases_that_are_not_claims(text):
    assert types(text) == []


def test_real_claims_next_to_those_phrases_still_count():
    assert types("Added unit tests for the parser.") == [TESTS_ADDED]
    assert types("Updated the README with setup steps.") == [DOCS_UPDATED]


def test_ci_claims_from_editable_texts_carry_the_final_head():
    source = TextSource(
        origin="body", text="All tests pass. Fixes #3", written_at=T0, ref="c1", final_ref="c2"
    )
    claims = {c.type: c for c in extract_claims("pr-1", [source])}
    assert (claims[TESTS_PASS].attributes["ref"], claims[TESTS_PASS].attributes["final_ref"]) == (
        "c1",
        "c2",
    )
    assert "final_ref" not in claims[FIXES_ISSUE].attributes
