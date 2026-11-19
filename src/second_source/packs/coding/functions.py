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
)
_DOC_PATH = re.compile(
    r"(^|/)(docs?|documentation)/"  # documentation directories
    r"|(^|/)(readme|changelog|contributing)(\.[a-z]+)?$"  # top-level project docs
    r"|\.(md|mdx|rst|adoc)$"  # documentation formats
)


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path.lower()))


def is_doc_path(path: str) -> bool:
    return bool(_DOC_PATH.search(path.lower()))


# Registration ---------------------------------------------------------------------------------

_FUNCTIONS = {
    "is_test_check": is_test_check,
    "is_build_check": is_build_check,
    "is_lint_check": is_lint_check,
    "is_test_path": is_test_path,
    "is_doc_path": is_doc_path,
}


def register(registry: FunctionRegistry) -> None:
    """Make the helpers available to expressions (referenced from pack.yaml).

    Each helper receives a CEL string and must return a CEL boolean, so wrap the plain
    Python functions above.
    """
    for name, function in _FUNCTIONS.items():
        registry.register(name, lambda value, f=function: to_cel(f(str(value))))
