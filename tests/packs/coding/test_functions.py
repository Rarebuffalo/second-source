"""Tests for the coding pack's classification helpers (packs/coding/functions.py)."""

import pytest

from second_source.core.expr import Evaluator, FunctionRegistry
from second_source.packs.coding.functions import (
    env_matches,
    is_build_check,
    is_ci_path,
    is_doc_path,
    is_lint_check,
    is_lint_config_path,
    is_test_check,
    is_test_config_path,
    is_test_path,
    job_matches,
    normalise_environment,
    register,
    same_repo,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("pytest (3.12)", True),
        ("CI / unit-tests", True),
        ("Run Jest", True),
        ("e2e", True),
        ("integration", True),
        ("go test ./...", True),
        ("tox -e py312", True),
        ("Tests", True),
        ("build", False),
        ("lint", False),
        ("attestation", False),  # contains "test" but is not a test job
        ("latest-release", False),
        ("CI", False),  # too ambiguous to count
    ],
)
def test_is_test_check(name, expected):
    assert is_test_check(name) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("build (ubuntu)", True),
        ("Compile", True),
        ("webpack bundle", True),
        ("rebuild-cache", False),
    ],
)
def test_is_build_check(name, expected):
    assert is_build_check(name) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("lint", True),
        ("ESLint", True),
        ("ruff check", True),
        ("golangci-lint", True),
        ("typecheck", True),
        ("cargo fmt", True),
        ("Prettier", True),
        ("formatter-docs", False),
        ("pytest", False),
    ],
)
def test_is_lint_check(name, expected):
    assert is_lint_check(name) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_parser.py", True),
        ("src/pkg/test_utils.py", True),
        ("pkg/server_test.go", True),
        ("src/app.test.ts", True),
        ("web/Button.spec.jsx", True),
        ("src/__tests__/api.js", True),
        ("src/test/java/com/x/ParserTest.java", True),
        ("spec/models/user_spec.rb", True),
        # conventions missed in benchmark run 1
        ("OfficeIMO.Tests/Word/TableTests.cs", True),
        ("core/renderer/dom_unittest.cc", True),
        ("crates/io/src/reader_tests.rs", True),
        ("frontend/marimo/_smoke_tests/plots.py", True),
        ("e2e/login.ts", True),
        ("cypress/support/commands.ts", True),
        ("web/login.cy.ts", True),
        ("testdata/sqllogictests/joins.slt", True),
        ("src/attestation.rs", False),
        (".github/workflows/run_tests.yml", False),
        ("requirements_test.txt", False),
        ("playwright/config.ts", False),
        ("foo_test.md", False),
        ("src/parser.py", False),
        ("src/latest.py", False),
        ("docs/testing.md", False),  # documentation about testing, not a test
    ],
)
def test_is_test_path(path, expected):
    assert is_test_path(path) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("README.md", True),
        ("readme", True),
        ("docs/guide/setup.md", True),
        ("CHANGELOG.rst", True),
        ("site/page.mdx", True),
        ("changelog/7510-add-consent.yaml", True),
        (".changelogs/12151.json", True),
        ("newsfragments/123.feature", True),
        (".changeset/brave-dogs-run.md", True),
        ("changelog_entry.yaml", True),
        ("src/changes_handler.py", False),
        ("src/changes/diff.ts", False),
        ("src/readme_parser.py", False),
        ("src/app.py", False),
    ],
)
def test_is_doc_path(path, expected):
    assert is_doc_path(path) is expected


@pytest.mark.parametrize(
    ("claim_type", "job", "expected"),
    [
        ("TESTS_PASS", "pytest", True),
        ("TESTS_PASS", "eslint", False),
        ("BUILD_OK", "build (ubuntu)", True),
        ("LINT_OK", "ruff check", True),
        ("DOCS_UPDATED", "pytest", False),  # claim types without CI jobs never match
    ],
)
def test_job_matches(claim_type, job, expected):
    assert job_matches(claim_type, job) is expected


def test_same_repo_ignores_case_and_whitespace():
    assert same_repo("Octo/Infra", "octo/infra ")
    assert not same_repo("octo/infra", "octo/app")


@pytest.mark.parametrize(
    ("evidence_env", "claimed_env", "expected"),
    [
        ("prod", "production", True),  # short form in the evidence
        ("production", "prod", True),  # short form in the claim
        ("Prod-EU", "production", True),
        ("staging-eu", "staging", True),
        ("stg", "staging", True),
        ("production", "staging", False),
        ("dev", "development", True),
    ],
)
def test_env_matches_normalises_both_sides(evidence_env, claimed_env, expected):
    assert env_matches(evidence_env, claimed_env) is expected


def test_normalise_environment():
    assert normalise_environment(" Prod-EU ") == "production-eu"
    assert normalise_environment("preview") == "preview"


def test_two_argument_helpers_work_in_expressions():
    registry = FunctionRegistry()
    register(registry)
    evaluator = Evaluator(registry)
    assert evaluator.evaluate_bool("same_repo('A/B', 'a/b')", {})
    assert evaluator.evaluate_bool("job_matches('TESTS_PASS', 'jest')", {})


def test_functions_are_callable_from_expressions():
    registry = FunctionRegistry()
    register(registry)
    evaluator = Evaluator(registry)
    assert evaluator.evaluate_bool("is_test_check('pytest')", {})
    assert not evaluator.evaluate_bool("is_doc_path('src/main.py')", {})


@pytest.mark.parametrize(
    ("path", "ci", "test_config", "lint_config"),
    [
        (".github/workflows/ci.yml", True, False, False),
        (".github/actions/setup/action.yml", True, False, False),
        (".gitlab-ci.yml", True, False, False),
        ("tox.ini", False, True, False),
        ("packages/web/jest.config.ts", False, True, False),
        ("conftest.py", False, True, False),
        (".eslintrc.json", False, False, True),
        ("ruff.toml", False, False, True),
        ("pyproject.toml", False, False, False),  # mostly dependencies: judged line by line
        ("package.json", False, False, False),
        ("docs/workflows/ci.yml", False, False, False),
    ],
)
def test_ci_test_and_lint_config_paths(path, ci, test_config, lint_config):
    assert (is_ci_path(path), is_test_config_path(path), is_lint_config_path(path)) == (
        ci,
        test_config,
        lint_config,
    )
