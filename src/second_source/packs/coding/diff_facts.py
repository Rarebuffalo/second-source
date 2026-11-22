"""Integrity facts from a file's unified diff: counts, never code.

GitHub's `/pulls/N/files` returns each file's `patch`. Second Source never stores code, so the
GitHub mapper passes the patch through ``facts`` and keeps only the numbers it returns. The
integrity guards (checks/*_guard.yaml) read those numbers to spot a PR that shaped its own
evidence: skipped or removed tests, a weakened test command, a lowered coverage gate.

Every pattern is deliberately narrow (design: private/research/integrity-design-comparison.md):

- **Skips** count only *unconditional, declaration-level* markers (``@pytest.mark.skip``,
  ``it.skip("…"``, ``#[ignore]``, ``@Disabled``). Runtime skips (``pytest.skip(``,
  ``t.Skip(``) are excluded: in a sample of agent PRs most of them were legitimate environment
  guards ("no GPU"). ``.only`` / ``fit`` count, because they silently skip every other test.
- **Weakened test commands** (``continue-on-error: true``, ``|| true``, ``--deselect``,
  ``-k "not …"``) count only within a few lines of a test command in the same hunk.
- **Coverage gates** count only when one hunk removes and adds the same key, in the config file
  that owns it, with a weaker number (lower, or higher for Codecov's allowed drop).

Lines are matched on ``+`` / ``-`` lines inside hunks.
"""

from __future__ import annotations

import re
from typing import Any

from second_source.packs.coding.functions import (
    is_ci_path,
    is_lint_config_path,
    is_test_config_path,
    is_test_path,
)

MAX_MARKER_LINES = 5  # new-file line numbers kept for links into the diff

# ---- languages ------------------------------------------------------------------------------

_LANGUAGES = {
    "py": "python",
    "js": "js", "jsx": "js", "ts": "js", "tsx": "js", "mjs": "js", "cjs": "js",
    "mts": "js", "cts": "js",
    "go": "go",
    "rs": "rust",
    "java": "jvm", "kt": "jvm", "kts": "jvm", "scala": "jvm",
    "cs": "csharp",
    "rb": "ruby",
    "php": "php",
}  # fmt: skip


def language_of(path: str) -> str | None:
    extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _LANGUAGES.get(extension)


# ---- patterns per language (applied to the text after the leading "+" or "-") ---------------

_Q = r"""['"`]"""  # a string literal starts here: "it.skip('name'" but not "it.skip()"

# Declaration-level markers, anchored at the start of the line (after an optional "await"), so
# a marker inside a comment or a string ("code: \"describe.skip('x')\"") does not count. A
# Python decorator must end the line ("@pytest.mark.skip is used for…" in a docstring is prose).
_SKIP = {
    "python": re.compile(
        r"^\s*@(pytest\.)?mark\.(skip|xfail)\b(?!if)\s*(\(.*\))?\s*$"
        r"|^\s*@unittest\.(skip\b(?!If|Unless)|expectedFailure)\b\s*(\(.*\))?\s*$"
        r"|^\s*pytestmark\s*=.*\bpytest\.mark\.(skip|xfail)\b(?!if)"
    ),
    "js": re.compile(
        rf"^\s*(await\s+)?(it|test|describe|context|suite|specify)\.(skip|only)(\.each\b.*)?\s*\(\s*{_Q}"
        rf"|^\s*(await\s+)?(xit|xdescribe|xtest|xcontext|fit|fdescribe)\s*\(\s*{_Q}"
    ),
    "rust": re.compile(r"^\s*#\[ignore\b"),
    "jvm": re.compile(r"^\s*@(Disabled|Ignore)\b"),
    "csharp": re.compile(r"^\s*\[\s*(Ignore|Skip)\b|\b(Fact|Theory)\s*\(\s*Skip\s*="),
    "ruby": re.compile(r"^\s*(xit|xdescribe|xcontext)\b"),
}
# Files that test a test framework itself write skip markers inside strings on purpose
# (pytester.makepyfile, ESLint's RuleTester): their markers are not skips of this project's tests.
_FRAMEWORK_SELF_TEST = re.compile(r"\b(pytester|makepyfile|testdir|RuleTester)\b")
_COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*|#(?!\[))")  # Rust attributes start with "#["

_TEST_CASE = {
    "python": re.compile(r"^\s*(async\s+)?def\s+test\w*\s*\("),
    "js": re.compile(rf"(^|[^.\w])(it|test)\s*\(\s*{_Q}"),
    "go": re.compile(r"^func\s+(Test|Fuzz)\w*\s*\("),
    "rust": re.compile(r"#\[(\w+::)?test\b"),
    "jvm": re.compile(r"@(Test|ParameterizedTest|RepeatedTest)\b"),
    "csharp": re.compile(r"\[(Fact|Theory|Test|TestMethod|TestCase)\b"),
    "ruby": re.compile(r"""^\s*(it|specify|test)\s*(\(|['"])"""),
    "php": re.compile(r"function\s+test\w*\s*\(|@test\b"),
}

_ASSERT = re.compile(
    r"\bassert\w*\b|\bexpect\s*\(|\.should\b|\bt\.(Error|Errorf|Fatal|Fatalf|Fail)\b"
    r"|\brequire\.\w+\(|\bassert(_eq|_ne)?!\(|\.to(Be|Equal|Have|Throw|Match|Contain)\w*\("
    r"|\bAssert\.\w+\(|\$this->assert\w+\("
)

# Ways to make CI tolerate failing or missing tests. They count only on the same logical
# command as a test runner (the same line, or a line continued with a backslash), except
# continue-on-error, which counts in the same workflow step as a test command.
_SAME_COMMAND_WEAKENING = re.compile(
    r"\|\|\s*(true|exit 0|:)\s*(#.*)?$|--passWithNoTests|--collect-only|--deselect\b"
    r"|\s-k\s+['\"]?not\b|\s-m\s+['\"]?not\b|--ignore[= ]|-DskipTests\b|-Dmaven\.test\.skip=true"
    r"|\s-x\s+test\b|\s--\s+--skip\b|\s-skip\s"
)
_CONTINUE_ON_ERROR = re.compile(r"^\s*continue-on-error:\s*(true|\$\{\{\s*true\s*\}\})\s*(#.*)?$")
_STEP_START = re.compile(r"^\s*-\s")  # a new workflow step (or list item)
_TEST_COMMAND = re.compile(
    r"\b(pytest|tox|nox|npm (run )?test|yarn test|pnpm (run )?test|jest|vitest|mocha|go test"
    r"|cargo (test|nextest)|mvn\b.*\b(test|verify|install|package)|gradlew? .*\b(test|check|build)"
    r"|rspec|phpunit|dotnet test|ctest|make (test|check))\b"
)
# Installing a test runner is not running it ("pip install pytest").
_INSTALL = re.compile(
    r"\b(pip3? install|uv (pip install|add|sync)|npm (i|install|ci)\b|yarn add|pnpm (add|install)"
    r"|poetry (add|install)|apt(-get)? install|brew install|cargo install|go install)\b"
)
MAX_LINE = 2000  # longer lines (minified or generated) are not read

# Files where a test command or a coverage gate can be configured.
_COMMAND_FILES = re.compile(
    r"(^|/)(\.github/workflows/[^/]+\.ya?ml|\.github/actions/.+\.ya?ml|\.gitlab-ci\.yml|\.circleci/[^/]+|tox\.ini|noxfile\.py"
    r"|pytest\.ini|setup\.cfg|pyproject\.toml|package\.json|makefile|justfile|\.travis\.yml"
    r"|azure-pipelines[^/]*\.ya?ml|codecov\.ya?ml|\.codecov\.ya?ml|\.coveragerc"
    r"|jest\.config\.[cm]?[jt]s|vitest\.config\.[cm]?[jt]s|\.nycrc[^/]*)$",
    re.IGNORECASE,
)

# Coverage gates. A key counts only in the config files that own it, with the number right
# after it ("fail_under = 90", '"lines": 80', "target: 85%"), so unrelated keys such as a build
# matrix "target: x86_64-…" never match. Codecov's "threshold" is the allowed *drop*, so for it a
# higher number is a weaker gate.
_NUMBER = r"""\s*["']?\s*[:=]\s*["']?(\d+(?:\.\d+)?)%?["']?\s*(?:,|#|$)"""
_COVERAGE_PY_FILES = re.compile(r"(^|/)(pyproject\.toml|setup\.cfg|\.coveragerc|tox\.ini)$", re.I)
_JS_COVERAGE_FILES = re.compile(
    r"(^|/)(jest\.config\.[\w.]+|vitest\.config\.[\w.]+|\.nycrc[\w.]*|nyc\.config\.[\w.]+"
    r"|package\.json)$",
    re.I,
)
_CODECOV_FILES = re.compile(r"(^|/)\.?codecov\.ya?ml$", re.I)
# (key name, pattern with the number as group 1, files it applies to, higher number = stricter)
_GATES = (
    ("fail_under", re.compile(r"^\s*fail[_-]under" + _NUMBER), _COVERAGE_PY_FILES, True),
    (
        "js_threshold",
        re.compile(r"""^\s*["']?(?:branches|lines|functions|statements)["']?""" + _NUMBER),
        _JS_COVERAGE_FILES,
        True,
    ),
    ("codecov_target", re.compile(r"^\s*target" + _NUMBER), _CODECOV_FILES, True),
    ("codecov_threshold", re.compile(r"^\s*threshold" + _NUMBER), _CODECOV_FILES, False),
)
# A fail-under flag on a test command, in any file that runs tests.
_FAIL_UNDER_FLAG = re.compile(r"--(?:cov-)?fail-under[= ](\d+(?:\.\d+)?)")
# Switching a Codecov status check off (only counts when an existing config is edited).
_COVERAGE_OFF = re.compile(r"^\s*(informational:\s*true|(project|patch):\s*off)\b")


def _gate(path: str, text: str) -> tuple[str, float, bool] | None:
    """(key, number, higher-is-stricter) if the line sets a coverage gate in this file."""
    flag = _FAIL_UNDER_FLAG.search(text)
    if flag:
        return "flag_fail_under", float(flag.group(1)), True
    for name, pattern, files, stricter in _GATES:
        if files.search(path):
            found = pattern.search(text)
            if found:
                key = name
                if name == "js_threshold":  # one gate per metric
                    key += ":" + re.search(r"branches|lines|functions|statements", text).group(0)
                return key, float(found.group(1)), stricter
    return None


# Lint suppressions added in non-test code (a caution for "lint passes").
_LINT_SUPPRESSION = re.compile(
    r"#\s*noqa\b|#\s*type:\s*ignore|//\s*@ts-(ignore|expect-error|nocheck)|eslint-disable"
    r"|#!?\[allow\(|//\s*nolint\b|#\s*pylint:\s*disable|@SuppressWarnings|//\s*biome-ignore"
)

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def facts(path: str, status: str, patch: str | None) -> dict[str, Any]:
    """Integrity counts for one changed file. Absent counts mean zero."""
    if patch is None:
        return {"patch_available": False}
    language = language_of(path)
    test_file = is_test_path(path)
    command_file = bool(_COMMAND_FILES.search(path))
    counts: dict[str, Any] = {"patch_available": True}
    markers: list[int] = []
    # A file that tests a test framework writes skip markers inside strings on purpose.
    count_skips = not _FRAMEWORK_SELF_TEST.search(patch)

    def bump(key: str, line_number: int | None = None) -> None:
        counts[key] = counts.get(key, 0) + 1
        if line_number is not None and len(markers) < MAX_MARKER_LINES:
            markers.append(line_number)

    for hunk in _hunks(patch):
        removed_gates: dict[str, tuple[float, bool]] = {}
        added_gates: dict[str, tuple[float, bool]] = {}
        edits_existing = any(sign == "-" for sign, _t, _n in hunk)
        commands = _command_rows(hunk) if command_file else []
        steps_with_tests = _steps_with_tests(hunk, commands) if command_file else {}
        step = 0
        for index, (sign, text, new_line) in enumerate(hunk):
            if command_file and _STEP_START.match(text):
                step += 1
            if sign == " " or len(text) > MAX_LINE:
                continue
            if test_file and language and not _COMMENT_LINE.match(text):
                skip = count_skips and language in _SKIP and _SKIP[language].search(text)
                if skip:
                    bump("test_skips_added" if sign == "+" else "test_skips_removed", new_line)
                # A skipped declaration ("it.skip('x', …)") is still a test case.
                if skip or (language in _TEST_CASE and _TEST_CASE[language].search(text)):
                    bump("test_cases_added" if sign == "+" else "test_cases_removed")
                if _ASSERT.search(text):
                    bump("asserts_added" if sign == "+" else "asserts_removed")
            if sign == "+" and not test_file and language and _LINT_SUPPRESSION.search(text):
                bump("lint_suppressions_added")
            if command_file and sign == "+":
                same_command = commands[index] and _SAME_COMMAND_WEAKENING.search(text)
                same_step = steps_with_tests.get(step) and _CONTINUE_ON_ERROR.search(text)
                if same_command or same_step:
                    bump("test_cmd_weakened", new_line)
            if command_file or _CODECOV_FILES.search(path) or _JS_COVERAGE_FILES.search(path):
                gate = _gate(path, text)
                if gate:
                    key, number, stricter = gate
                    (removed_gates if sign == "-" else added_gates)[key] = (number, stricter)
                if (
                    sign == "+"
                    and status != "added"  # a first Codecov config lowers nothing
                    and edits_existing
                    and _CODECOV_FILES.search(path)
                    and _COVERAGE_OFF.search(text)
                ):
                    counts["coverage_gate_lowered"] = True
            if sign == "+" and text.strip():
                counts["nonblank_added"] = counts.get("nonblank_added", 0) + 1
        for key, (number, stricter) in added_gates.items():
            if key in removed_gates:
                before = removed_gates[key][0]
                if (number < before) if stricter else (number > before):
                    counts["coverage_gate_lowered"] = True
    if markers:
        counts["marker_lines"] = markers
    return counts


def _command_rows(hunk: list[tuple[str, str, int | None]]) -> list[bool]:
    """Per row: is it (part of) a test command? A line continued with a backslash belongs to
    the command it continues, so "pytest \\" then "  --deselect x" is one command."""
    rows: list[bool] = []
    continuing = False
    for _sign, text, _line in hunk:
        own = bool(_TEST_COMMAND.search(text)) and not _INSTALL.search(text)
        rows.append(own or continuing)
        continuing = (own or continuing) and text.rstrip().endswith("\\")
    return rows


def _steps_with_tests(
    hunk: list[tuple[str, str, int | None]], commands: list[bool]
) -> dict[int, bool]:
    """Workflow step number -> whether that step (within the hunk) runs a test command."""
    steps: dict[int, bool] = {}
    step = 0
    for (_sign, text, _line), is_command in zip(hunk, commands, strict=True):
        if _STEP_START.match(text):
            step += 1
        steps[step] = steps.get(step, False) or is_command
    return steps


def _hunks(patch: str) -> list[list[tuple[str, str, int | None]]]:
    """Split a unified diff into hunks of (sign, text, new-file line number or None)."""
    hunks: list[list[tuple[str, str, int | None]]] = []
    new_line = 0
    # split("\n"), not splitlines(): a form feed or a Unicode line separator inside a line is
    # content, and must not create a fabricated diff line.
    for raw in (line.removesuffix("\r") for line in patch.split("\n")):
        header = _HUNK.match(raw)
        if header:
            hunks.append([])
            new_line = int(header.group(1))
            continue
        # GitHub's patch starts at the first hunk (no file headers), so a removed SQL comment
        # ("--- x") is content, not a header. "\ No newline at end of file" is metadata.
        if not hunks or raw.startswith("\\"):
            continue
        sign, text = raw[:1], raw[1:]
        if sign == "+":
            hunks[-1].append(("+", text, new_line))
            new_line += 1
        elif sign == "-":
            hunks[-1].append(("-", text, None))
        else:
            hunks[-1].append((" ", text, new_line))
            new_line += 1
    return hunks


# ---- one PR-level summary -------------------------------------------------------------------
# CEL has no sum(), so the guards read one record with the PR's totals instead of adding up
# per-file counts in YAML.


def summarize(files: list[dict[str, Any]]) -> dict[str, Any]:
    """PR-level totals over per-file records (path, status, additions, deletions + facts)."""
    total: dict[str, Any] = {
        "files": len(files),
        "test_skips_net": 0,
        "test_cases_added": 0,
        "test_cases_removed": 0,
        "asserts_added": 0,
        "test_cmd_weakened": 0,
        "lint_suppressions_added": 0,
        "removed_test_files": 0,
        "test_source_changed": 0,  # test files in a known language with added lines
        "code_net": 0,  # added minus deleted lines in non-test source files
        "code_removed": False,  # a non-test source file was deleted
        "coverage_gate_lowered": False,
        "ci_config_changed": False,
        "lint_config_changed": False,
        "uninspected": 0,  # test, CI or test-config files GitHub sent no patch for
    }
    changed_ci: list[str] = []
    for file in files:
        path, status = file["path"], file.get("status", "modified")
        test_file = is_test_path(path)
        config = is_ci_path(path) or is_test_config_path(path)
        if config:
            total["ci_config_changed"] = True
            changed_ci.append(path)
        if is_lint_config_path(path):
            total["lint_config_changed"] = True
        # A record without facts (an old snapshot) was not inspected: never read it as clean.
        if (test_file or config) and not file.get("patch_available", False):
            total["uninspected"] += 1
        if test_file:
            if status == "removed":
                total["removed_test_files"] += 1
            elif language_of(path) and file.get("additions", 0) > 0:
                total["test_source_changed"] += 1
        elif language_of(path):
            total["code_net"] += file.get("additions", 0) - file.get("deletions", 0)
            if status == "removed":
                total["code_removed"] = True
        total["test_skips_net"] += file.get("test_skips_added", 0) - file.get(
            "test_skips_removed", 0
        )
        for key in (
            "test_cases_added",
            "test_cases_removed",
            "asserts_added",
            "test_cmd_weakened",
            "lint_suppressions_added",
        ):
            total[key] += file.get(key, 0)
        if file.get("coverage_gate_lowered"):
            total["coverage_gate_lowered"] = True
    if changed_ci:
        total["ci_config_files"] = sorted(changed_ci)[:5]
    return total
