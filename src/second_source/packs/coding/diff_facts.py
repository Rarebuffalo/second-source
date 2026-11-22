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
# Python decorator must end the line, may carry a trailing comment, or may open a multi-line
# argument list ("@pytest.mark.skip(" then "reason=…"); prose such as "@pytest.mark.skip is used
# for…" in a docstring is none of these.
_PY_DECORATOR_END = r"\s*(\(.*)?\s*(#.*)?$"
_SKIP = {
    "python": re.compile(
        rf"^\s*@(pytest\.)?mark\.(skip|xfail)\b(?!if){_PY_DECORATOR_END}"
        rf"|^\s*@unittest\.(skip\b(?!If|Unless)|expectedFailure)\b{_PY_DECORATOR_END}"
        r"|^\s*pytestmark\s*=.*\bpytest\.mark\.(skip|xfail)\b(?!if)"
    ),
    "js": re.compile(
        rf"^\s*(await\s+)?(test\.)?(it|test|describe|context|suite|specify)\.(skip|only|fixme)"
        rf"(\.each\b.*)?\s*\(\s*{_Q}"
        rf"|^\s*(await\s+)?test\.fixme\s*\(\s*{_Q}"
        rf"|^\s*(await\s+)?(xit|xdescribe|xtest|xcontext|fit|fdescribe)\s*\(\s*{_Q}"
    ),
    "rust": re.compile(r"^\s*#\[ignore\b"),
    "jvm": re.compile(r"^\s*@(Disabled|Ignore)\b"),
    "csharp": re.compile(r"^\s*\[\s*(Ignore|Skip)\b|\b(Fact|Theory)\s*\(\s*Skip\s*="),
    "ruby": re.compile(r"^\s*(xit|xdescribe|xcontext)\b"),
}
# Skip markers that also declare the test on the same line ("it.skip('x', …)", "xit('x'",
# "[Fact(Skip = …)]"). In Python, Java, Rust and C# attributes the marker is a separate line from
# the declaration, so only these forms count as a test case themselves.
_DECLARING_SKIP = {
    "js": _SKIP["js"],
    "csharp": re.compile(r"\b(Fact|Theory)\s*\(\s*Skip\s*="),
    "ruby": re.compile(r"^\s*(xit|xdescribe|xcontext)\b"),
}
# Tests of a test framework write skip markers inside strings on purpose. Python: markers inside
# a triple-quoted string are ignored (tracked per hunk). JS: files using ESLint's RuleTester.
_JS_SELF_TEST = re.compile(r"\bRuleTester\b")
_TRIPLE_QUOTE = re.compile(r"\"\"\"|\'\'\'")
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
# continue-on-error, which counts in the same workflow step, or for the whole job when it sits at
# job level.
_SAME_COMMAND_WEAKENING = re.compile(
    r"\|\|\s*(true|exit 0|:)\s*(#.*)?$|--passWithNoTests|--collect-only|--deselect\b"
    r"|\s-k\s+['\"]?not\b|\s-m\s+['\"]?not\b|--ignore[= ]|\s--\s+--skip\b|\s-skip\s"
)
# Build-tool flags that skip tests. Routine on build-only steps (package an artifact, test
# elsewhere), so they count only when the PR added them to a command that ran without them
# (a removed line in the same hunk with the same tool and without the flag).
_BUILD_SKIP_FLAG = re.compile(r"\s-DskipTests\b|\s-Dmaven\.test\.skip=true|(?<=\S)\s+-x\s+test\b")
_BUILD_TOOL = re.compile(r"\b(mvnw?|gradlew?)\b")
_CONTINUE_ON_ERROR = re.compile(r"^(\s*)continue-on-error:\s*(true|\$\{\{\s*true\s*\}\})\s*(#.*)?$")
_JOB_KEY = re.compile(
    r"^(\s*)(runs-on|steps|needs|strategy):"
)  # timeout-minutes is also a step key
_STEP_START = re.compile(r"^(\s*)-\s")  # a list item; steps are those at the steps' indentation
_STEP_KEYS = re.compile(r"^\s*-\s+(name|run|uses):")
_TEST_COMMAND = re.compile(
    r"\b(pytest|tox|nox|npm (run )?test|yarn test|pnpm (run )?test|jest|vitest|mocha|go test"
    r"|cargo (test|nextest)|mvnw?\b.*\b(test|verify)|gradlew?\b.*\b(test|check)"
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
_CODECOV_AUTO = re.compile(r"^\s*target:\s*auto\b")
_COVERAGE_OFF = re.compile(r"^\s*(informational:\s*true|(project|patch):\s*off)\b")


def _gate(path: str, text: str) -> tuple[str, float, bool] | None:
    """(key, number, higher-is-stricter) if the line sets a coverage gate in this file."""
    flag = _FAIL_UNDER_FLAG.search(text)
    if flag:
        return "fail_under", float(flag.group(1)), True  # same gate as fail_under in config
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
    gate_file = command_file or bool(_CODECOV_FILES.search(path) or _JS_COVERAGE_FILES.search(path))
    counts: dict[str, Any] = {"patch_available": True}
    markers: list[int] = []
    js_self_test = language == "js" and bool(_JS_SELF_TEST.search(patch))
    # Gate families removed and added in this file. Whether a gate was deleted outright is
    # decided per PR in summarize(): moving fail_under from setup.cfg to pyproject.toml, or from
    # a --cov-fail-under flag into config, is not a deletion.
    file_removed_gates: set[str] = set()
    file_added_gates: set[str] = set()

    def bump(key: str, line_number: int | None = None) -> None:
        counts[key] = counts.get(key, 0) + 1
        if line_number is not None and len(markers) < MAX_MARKER_LINES:
            markers.append(line_number)

    for hunk in _hunks(patch):
        removed_gates: dict[str, tuple[float, bool]] = {}
        added_gates: dict[str, tuple[float, bool]] = {}
        removed_lines = [text for sign, text, _n in hunk if sign == "-"]
        commands = _command_rows(hunk) if command_file else []
        step_of = _step_numbers(hunk) if command_file else []
        steps_with_tests = {step_of[i] for i, is_command in enumerate(commands) if is_command}
        in_string = False  # inside a Python triple-quoted string (within this hunk)
        for index, (sign, text, new_line) in enumerate(hunk):
            starts_in_string = in_string
            if language == "python" and len(_TRIPLE_QUOTE.findall(text)) % 2:
                in_string = not in_string
            if sign == " " or len(text) > MAX_LINE:
                continue
            if test_file and language and not _COMMENT_LINE.match(text) and not starts_in_string:
                skip = not js_self_test and language in _SKIP and bool(_SKIP[language].search(text))
                if skip:
                    bump("test_skips_added" if sign == "+" else "test_skips_removed", new_line)
                # A test case is a declaration; a skip marker counts as one only when it declares
                # the test on the same line ("it.skip('x', …)"), not as a separate decorator.
                declares_skip = (
                    skip
                    and language in _DECLARING_SKIP
                    and bool(_DECLARING_SKIP[language].search(text))
                )
                if declares_skip or (language in _TEST_CASE and _TEST_CASE[language].search(text)):
                    bump("test_cases_added" if sign == "+" else "test_cases_removed")
                if _ASSERT.search(text):
                    bump("asserts_added" if sign == "+" else "asserts_removed")
            if sign == "+" and not test_file and language and _LINT_SUPPRESSION.search(text):
                bump("lint_suppressions_added")
            if command_file and sign == "+":
                # The test command itself weakened: withdraws "tests pass".
                if commands[index] and _SAME_COMMAND_WEAKENING.search(text):
                    bump("test_cmd_weakened", new_line)
                # Tolerated failures and build-flag test skips: cautions until measured.
                if _tolerates_failures(text, index, hunk, commands, step_of, steps_with_tests):
                    bump("ci_failures_tolerated", new_line)
                if _skips_tests_in_build(text, removed_lines):
                    bump("build_tests_skipped", new_line)
            if gate_file:
                gate = _gate(path, text)
                if gate:
                    key, number, stricter = gate
                    (removed_gates if sign == "-" else added_gates)[key] = (number, stricter)
                    (file_removed_gates if sign == "-" else file_added_gates).add(key)
                off = sign == "+" and _CODECOV_FILES.search(path) and _COVERAGE_OFF.search(text)
                # A first Codecov config lowers nothing; switching a status off in an existing
                # one does, as does replacing a target number with "auto".
                auto = (
                    sign == "+" and _CODECOV_AUTO.search(text) and "codecov_target" in removed_gates
                )
                if (off and status != "added") or auto:
                    counts["coverage_gate_lowered"] = True
            if sign == "+" and text.strip():
                counts["nonblank_added"] = counts.get("nonblank_added", 0) + 1
        for key, (number, stricter) in added_gates.items():
            if key in removed_gates:
                before = removed_gates[key][0]
                if (number < before) if stricter else (number > before):
                    counts["coverage_gate_lowered"] = True
    if file_removed_gates:
        counts["gates_removed"] = sorted(file_removed_gates)  # fixed family names, not code
    if file_added_gates:
        counts["gates_added"] = sorted(file_added_gates)
    if markers:
        counts["marker_lines"] = markers
    return counts


def _tolerates_failures(
    text: str,
    index: int,
    hunk: list[tuple[str, str, int | None]],
    commands: list[bool],
    step_of: list[int],
    steps_with_tests: set[int],
) -> bool:
    """An added continue-on-error in the same workflow step as a test command, or at job level
    in a job (within the hunk) that runs tests."""
    flag = _CONTINUE_ON_ERROR.match(text)
    if not flag:
        return False
    indent = len(flag.group(1))
    job_level = any(len(m.group(1)) == indent for _s, t, _n in hunk if (m := _JOB_KEY.match(t)))
    if not job_level:
        return step_of[index] in steps_with_tests

    # Job level: look for a test command in the same job only, scanning both ways until a key at
    # a shallower indentation than the job's own keys (the job header, or the next job).
    def job_rows(rows: range) -> list[int]:
        found = []
        for row in rows:
            line = hunk[row][1]
            stripped = line.lstrip()
            if stripped and len(line) - len(stripped) < indent and stripped.endswith(":"):
                break
            found.append(row)
        return found

    same_job = job_rows(range(index + 1, len(hunk))) + job_rows(range(index - 1, -1, -1))
    return any(commands[row] for row in same_job)


def _skips_tests_in_build(text: str, removed_lines: list[str]) -> bool:
    """-DskipTests / -x test added to a command that ran before without it: the same command,
    with the flag removed, appears on a removed line of the hunk."""
    if not (_BUILD_TOOL.search(text) and _BUILD_SKIP_FLAG.search(text)):
        return False
    stripped = _normalise(_BUILD_SKIP_FLAG.sub(" ", text))
    return any(_normalise(removed) == stripped for removed in removed_lines)


def _normalise(command: str) -> str:
    return " ".join(command.split())


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


def _step_numbers(hunk: list[tuple[str, str, int | None]]) -> list[int]:
    """Per row: the workflow step it belongs to. Steps start at list items at the indentation
    of the first item after a "steps:" line (so a matrix "include: - name:" or a "- --fix"
    inside a step's with: does not start a step). Without a "steps:" line in the hunk, the
    first item with name:/run:/uses: sets the indentation."""
    indent = None
    after_steps = False
    for _sign, text, _line in hunk:
        if re.match(r"^\s*steps:\s*$", text):
            after_steps = True
            continue
        item = _STEP_START.match(text)
        if item and after_steps:
            indent = len(item.group(1))
            break
    if indent is None:
        indent = next(
            (
                len(m.group(1))
                for _s, text, _n in hunk
                if (m := _STEP_START.match(text)) and _STEP_KEYS.search(text)
            ),
            None,
        )
    numbers: list[int] = []
    step = 0
    for _sign, text, _line in hunk:
        item = _STEP_START.match(text)
        if item and (indent is None or len(item.group(1)) == indent):
            step += 1
        numbers.append(step)
    return numbers


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
        "ci_failures_tolerated": 0,  # continue-on-error on a test step or test job
        "build_tests_skipped": 0,  # -DskipTests / -x test added to an existing command
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
    gates_removed: set[str] = set()
    gates_added: set[str] = set()
    for file in files:
        gates_removed.update(file.get("gates_removed", ()))
        gates_added.update(file.get("gates_added", ()))
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
            "ci_failures_tolerated",
            "build_tests_skipped",
            "lint_suppressions_added",
        ):
            total[key] += file.get(key, 0)
        if file.get("coverage_gate_lowered"):
            total["coverage_gate_lowered"] = True
    if gates_removed - gates_added:
        total["coverage_gate_lowered"] = True  # a gate deleted in the PR and added nowhere
    if changed_ci:
        total["ci_config_files"] = sorted(changed_ci)[:5]
    return total
