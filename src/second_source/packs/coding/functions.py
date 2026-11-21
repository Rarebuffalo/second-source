"""Helper functions the coding pack's checks can call from their expressions.

They classify CI job names (is this a test job?) and file paths (is this a test file?).
All of them are pure, case-insensitive and deliberately conservative: a name that does not
clearly match is *not* counted, so checks report UNVERIFIABLE instead of guessing.
"""

from __future__ import annotations

import re

from second_source.core.expr import FunctionRegistry, to_cel


def _words(*terms: str) -> re.Pattern[str]:
    """Match any term as a whole word inside a job name.

    Job names are free text such as "CI / pytest (3.12)" or "unit-tests", so a "word" boundary
    here is any character that is not a letter. This makes "test" match "unit-tests" but
    not "attestation".
    """
    alternatives = "|".join(terms)
    return re.compile(rf"(?<![a-z])(?:{alternatives})(?![a-z])")


# CI job names ---------------------------------------------------------------------------------

_TEST_JOB = _words(
    r"tests?",
    r"testing",
    r"pytest",
    r"unittest",
    r"jest",
    r"vitest",
    r"mocha",
    r"karma",
    r"cypress",
    r"playwright",
    r"specs?",
    r"rspec",
    r"junit",
    r"unit",
    r"integration",
    r"e2e",
    r"go test",
    r"cargo test",
    r"tox",
    r"nox",
)
_BUILD_JOB = _words(r"build", r"builds", r"compile", r"compilation", r"bundle", r"webpack")
_LINT_JOB = _words(
    r"lint",
    r"linter",
    r"linting",
    r"eslint",
    r"ruff",
    r"flake8",
    r"pylint",
    r"clippy",
    r"golangci(?:-lint)?",
    r"prettier",
    r"format",
    r"fmt",
    r"black",
    r"typecheck",
    r"type-check",
    r"mypy",
    r"tsc",
    r"stylelint",
    r"rubocop",
)


def is_test_check(name: str) -> bool:
    return bool(_TEST_JOB.search(name.lower()))


def is_build_check(name: str) -> bool:
    return bool(_BUILD_JOB.search(name.lower()))


def is_lint_check(name: str) -> bool:
    return bool(_LINT_JOB.search(name.lower()))


# File paths -----------------------------------------------------------------------------------

# Common test-file conventions across ecosystems. Matched against the full, lower-cased path.
_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|testing)/"  # test directories
    r"|(^|/)test_[^/]+\.py$"  # pytest: test_x.py
    r"|_test\.(py|go|exs?|dart)$"  # x_test.py, x_test.go, Elixir, Dart
    r"|\.(test|spec)\.[cm]?[jt]sx?$"  # x.test.ts, x.spec.jsx, x.test.mjs
    r"|(test|tests|spec)\.(java|kt|cs|scala|swift)$"  # FooTest.java, FooSpec.scala
    r"|_spec\.rb$"  # RSpec
    r"|(^|/)[^/]*[._-]tests?/"  # .NET Foo.Tests/, unit-tests/, integration_tests/
    r"|(^|/)(e2e|cypress)/"  # end-to-end suites
    # foo_unittest.cc, foo_test.rs, foo_tests.rs: source files only, not run_tests.yml or
    # requirements_test.txt
    r"|_(unit)?tests?\.(c|cc|cpp|cxx|h|hpp|rs|go|py|rb|php|java|kt|swift|m|mm|cs|scala|exs?|dart|"
    r"[cm]?[jt]sx?)$"
    r"|\.cy\.[cm]?[jt]sx?$"  # Cypress: x.cy.ts
    r"|\.slt$"  # sqllogictest files
)
_DOC_PATH = re.compile(
    r"(^|/)(docs?|documentation)/"  # documentation directories
    r"|(^|/)(readme|changelog|contributing)(\.[a-z]+)?$"  # top-level project docs
    r"|\.(md|mdx|rst|adoc)$"  # documentation formats
    # Changelog fragments (towncrier, changesets, per-PR entry files) are changelog entries,
    # which count as documentation (golden-set guide, rule 12).
    # At the repository root only: "src/changes/diff.ts" is code.
    r"|^\.?(changelogs?|changes|newsfragments|changelog\.d|changeset)/"
    r"|(^|/)changelog_entry\.ya?ml$"
)


def is_test_path(path: str) -> bool:
    lowered = path.lower()
    # CI configuration that runs tests is not a test ("".github/workflows/tests.yml").
    if lowered.startswith(".github/"):
        return False
    return bool(_TEST_PATH.search(lowered))


def is_doc_path(path: str) -> bool:
    return bool(_DOC_PATH.search(path.lower()))


# Matching a claim to CI jobs ------------------------------------------------------------------

# Which job classifier verifies which claim type. One shared CI check uses this, so the
# decision logic for tests, builds and linting lives in a single place.
_JOB_CLASSIFIERS = {
    "TESTS_PASS": is_test_check,
    "BUILD_OK": is_build_check,
    "LINT_OK": is_lint_check,
}


def job_matches(claim_type: str, job_name: str) -> bool:
    """True if a CI job with this name is relevant evidence for this claim type."""
    classifier = _JOB_CLASSIFIERS.get(claim_type)
    return classifier is not None and classifier(job_name)


# Repositories and environments ----------------------------------------------------------------


def same_repo(first: str, second: str) -> bool:
    """GitHub repository names are case-insensitive: "Octo/Infra" is "octo/infra"."""
    return first.strip().lower() == second.strip().lower()


# Common short names for deployment environments. Applied to both the claim ("deployed to
# prod") and the evidence (an environment named "prod"), so they always meet in the middle.
ENVIRONMENT_ALIASES = {
    "prod": "production",
    "prd": "production",
    "dev": "development",
    "stg": "staging",
    "stage": "staging",
}


def normalise_environment(name: str) -> str:
    """Lower-case an environment name and expand a known short form at its start.

    "Prod-EU" -> "production-eu"; "staging" -> "staging".
    """
    lowered = name.strip().lower()
    head, separator, tail = lowered.partition("-")
    return ENVIRONMENT_ALIASES.get(head, head) + separator + tail


def env_matches(evidence_env: str, claimed_env: str) -> bool:
    """True if a deployment environment satisfies the environment the agent named.

    Both sides are normalised, then the evidence must start with the claim, so a claim of
    "staging" is satisfied by "staging" or "staging-eu" but not by "production".
    """
    return normalise_environment(evidence_env).startswith(normalise_environment(claimed_env))


# Registration ---------------------------------------------------------------------------------

_FUNCTIONS = {
    "is_test_check": is_test_check,
    "is_build_check": is_build_check,
    "is_lint_check": is_lint_check,
    "is_test_path": is_test_path,
    "is_doc_path": is_doc_path,
    "job_matches": job_matches,
    "same_repo": same_repo,
    "env_matches": env_matches,
}


def register(registry: FunctionRegistry) -> None:
    """Make the helpers available to expressions (referenced from pack.yaml).

    Helpers receive CEL strings and must return CEL booleans, so each plain Python function
    above is wrapped: arguments are converted to str, the result back to a CEL value.
    """
    for name, function in _FUNCTIONS.items():
        registry.register(name, lambda *args, f=function: to_cel(f(*(str(arg) for arg in args))))
