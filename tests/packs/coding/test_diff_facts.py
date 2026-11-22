"""Tests for integrity facts from unified diffs (packs/coding/diff_facts.py)."""

import time

import pytest

from second_source.packs.coding.diff_facts import facts


def patch(*lines, start=10):
    """A one-hunk patch; lines start with '+', '-' or ' '."""
    return "\n".join([f"@@ -{start},5 +{start},5 @@", *lines])


def test_no_patch_means_unknown():
    assert facts("tests/test_x.py", "modified", None) == {"patch_available": False}


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("tests/test_x.py", "+@pytest.mark.skip(reason='later')"),
        ("tests/test_x.py", "+@pytest.mark.xfail"),
        ("tests/test_x.py", "+@unittest.skip('no')"),
        ("web/a.test.ts", "+  it.skip('renders', () => {"),
        ("web/a.test.ts", "+describe.only(`suite`, () => {"),  # focus skips everything else
        ("web/a.test.ts", "+  xit('renders', () => {"),
        ("src/lib_test.rs", "+#[ignore]"),
        ("src/test/java/AppTest.java", "+  @Disabled"),
        ("Tests/AppTests.cs", '+    [Fact(Skip = "flaky")]'),
        ("spec/app_spec.rb", "+  xit 'works' do"),
    ],
)
def test_unconditional_skips_are_counted(path, line):
    assert facts(path, "modified", patch(line))["test_skips_added"] == 1


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("tests/test_x.py", "+@pytest.mark.skipif(sys.platform == 'win32', reason='posix')"),
        ("tests/test_x.py", "+    pytest.skip('no GPU')"),  # runtime guard
        ("tests/test_x.py", "+@unittest.skipIf(not HAS_GPU, 'no GPU')"),
        ("pkg/x_test.go", '+\tt.Skip("needs network")'),
        ("web/a.test.ts", "+  test.skip()"),  # runtime call, not a declaration
        ("src/app.py", "+@pytest.mark.skip"),  # not a test file
    ],
)
def test_conditional_runtime_and_non_test_skips_are_not_counted(path, line):
    assert "test_skips_added" not in facts(path, "modified", patch(line))


def test_test_cases_and_assertions_are_counted_both_ways():
    lines = [
        "+def test_new_case():",
        "+    assert parse('') == []",
        "-def test_old_case():",
        "-    assert True",
        " def helper():",
    ]
    result = facts("tests/test_parser.py", "modified", patch(*lines))
    assert result["test_cases_added"] == result["test_cases_removed"] == 1
    assert result["asserts_added"] == result["asserts_removed"] == 1


def test_weakening_counts_only_near_a_test_command():
    near = patch("   - run: pytest -q", "+    continue-on-error: true")
    assert facts(".github/workflows/ci.yml", "modified", near)["test_cmd_weakened"] == 1
    far = patch(
        "+    continue-on-error: true", *[f" line {n}" for n in range(10)], " - run: pytest"
    )
    assert "test_cmd_weakened" not in facts(".github/workflows/ci.yml", "modified", far)
    shell = patch("+\tgo test ./... || true")
    assert facts("Makefile", "modified", shell)["test_cmd_weakened"] == 1
    # The same text in an ordinary source file is not a CI change.
    assert "test_cmd_weakened" not in facts("src/run.py", "modified", near)


def test_a_lowered_coverage_gate_is_detected_but_a_raised_one_is_not():
    lowered = patch("-fail_under = 90", "+fail_under = 80")
    assert facts("pyproject.toml", "modified", lowered)["coverage_gate_lowered"] is True
    raised = patch("-fail_under = 80", "+fail_under = 90")
    assert "coverage_gate_lowered" not in facts("pyproject.toml", "modified", raised)
    switched_off = patch("-      target: 80%", "+      informational: true")
    assert facts("codecov.yml", "modified", switched_off)["coverage_gate_lowered"] is True


@pytest.mark.parametrize(
    ("path", "status", "lines"),
    [
        # Guard code review H1: a build matrix target is not a coverage gate.
        (
            ".github/workflows/release.yml",
            "modified",
            [
                "-          target: x86_64-unknown-linux-gnu",
                "+          target: aarch64-unknown-linux-gnu",
            ],
        ),
        (
            ".github/workflows/ci.yml",
            "modified",
            ["-          target: stage2", "+          target: stage1"],
        ),
        ("Makefile", "modified", ["-\tstatements: 10", "+\tstatements: 5"]),
        # H2: Codecov's threshold is the allowed drop; a lower one is stricter.
        ("codecov.yml", "modified", ["-        threshold: 5%", "+        threshold: 1%"]),
        # H3: a first Codecov config lowers nothing.
        (
            "codecov.yml",
            "added",
            [
                "+coverage:",
                "+  status:",
                "+    patch:",
                "+      default:",
                "+        informational: true",
            ],
        ),
    ],
)
def test_coverage_changes_that_do_not_weaken_a_gate(path, status, lines):
    assert "coverage_gate_lowered" not in facts(path, status, patch(*lines))


@pytest.mark.parametrize(
    ("path", "lines"),
    [
        ("codecov.yml", ["-        threshold: 1%", "+        threshold: 5%"]),  # looser
        ("codecov.yml", ["-        target: 90%", "+        target: 80%"]),
        ("package.json", ['-        "lines": 90,', '+        "lines": 70,']),  # Jest, quoted key
        (
            ".github/workflows/ci.yml",
            [
                "-      - run: pytest --cov-fail-under=90",
                "+      - run: pytest --cov-fail-under=60",
            ],
        ),
        ("setup.cfg", ["-fail_under = 85", "+fail_under = 50"]),
    ],
)
def test_weakened_coverage_gates_are_detected(path, lines):
    assert facts(path, "modified", patch(*lines))["coverage_gate_lowered"] is True


def test_lint_suppressions_count_in_source_not_tests():
    line = patch("+x = eval(data)  # noqa: S307")
    assert facts("src/app.py", "modified", line)["lint_suppressions_added"] == 1
    assert "lint_suppressions_added" not in facts("tests/test_app.py", "modified", line)


def test_marker_lines_point_into_the_new_file():
    lines = [" import pytest", "", "+@pytest.mark.skip", " def test_x():"]
    result = facts("tests/test_x.py", "modified", patch(*lines, start=40))
    assert result["marker_lines"] == [42]


def test_sql_comments_removed_are_content_not_headers():
    # GitHub patches have no file headers; "--- x" is a removed line "-- x".
    lines = ["--- old comment", "+-- new comment", "+SELECT 1;"]
    assert facts("db/q.sql", "modified", patch(*lines))["nonblank_added"] == 2


def test_no_newline_marker_is_ignored():
    lines = ["+def test_a():", "+    assert 1", "\\ No newline at end of file"]
    assert facts("tests/test_a.py", "added", patch(*lines))["test_cases_added"] == 1


@pytest.mark.parametrize(
    ("path", "lines"),
    [
        # Guard code review H4: everyday CI that does not weaken a test run.
        (
            ".github/workflows/ci.yml",
            [
                "       - run: pip install pytest pytest-cov",
                "+      - run: docker rm -f db || true",
            ],
        ),
        (
            ".github/workflows/ci.yml",
            [
                "         run: npm test",
                "+      - name: Cleanup",
                "+        run: pkill -f server || true",
            ],
        ),
        (
            ".github/workflows/ci.yml",
            [
                "       - name: Lint",
                "+        continue-on-error: true",
                "         run: ruff check .",
                "       - name: Test",
                "         run: pytest",
            ],
        ),
        ("tox.ini", [" commands = pytest", "+    flake8 --ignore=E501"]),
    ],
)
def test_ci_changes_that_do_not_weaken_a_test_run(path, lines):
    assert "test_cmd_weakened" not in facts(path, "modified", patch(*lines))


@pytest.mark.parametrize(
    ("path", "lines"),
    [
        (".github/workflows/ci.yml", ["+      - run: pytest || true  # flaky"]),
        (
            ".github/workflows/ci.yml",
            [
                "       - name: Test",
                "         run: pytest",
                "+        continue-on-error: ${{ true }}",
            ],
        ),
        (
            ".github/workflows/ci.yml",
            ["+      - run: pytest \\", "+          --deselect tests/test_db.py"],
        ),
        (".github/workflows/ci.yml", ["+      - run: mvn -B verify -DskipTests"]),
        (".github/workflows/ci.yml", ["+      - run: ./gradlew build -x test"]),
        (".github/actions/test/action.yml", ["+      run: pytest -k 'not slow'"]),
    ],
)
def test_weakened_test_runs_are_detected(path, lines):
    assert facts(path, "modified", patch(*lines))["test_cmd_weakened"] == 1


@pytest.mark.parametrize(
    ("path", "line"),
    [
        # Guard code review M1: markers in comments, strings and prose.
        ("src/__tests__/a.test.ts", "+  // it.skip('flaky on CI', () => {})"),
        ("tests/rules/no-skip.test.js", "+    code: \"describe.skip('x', () => {})\","),
        ("tests/test_doc.py", "+    @pytest.mark.skip is used for slow tests"),
    ],
)
def test_skip_markers_in_comments_strings_and_prose_do_not_count(path, line):
    assert "test_skips_added" not in facts(path, "modified", patch(line))


def test_framework_self_tests_do_not_count_their_skip_strings():
    lines = [
        "+    pytester.makepyfile('''",
        "+        @pytest.mark.skip(reason='x')",
        "+        def test_a(): pass",
        "+    ''')",
    ]
    assert "test_skips_added" not in facts("testing/test_skipping.py", "modified", patch(*lines))


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("tests/test_x.py", "+pytestmark = pytest.mark.skip(reason='later')"),
        ("tests/test_x.py", "+@mark.skip"),
        ("web/a.test.ts", "+  it.skip.each([1, 2])('handles %s', (n) => {"),
        ("web/a.test.ts", "+  await test.skip('slow path', async () => {"),
    ],
)
def test_more_skip_forms_are_detected(path, line):
    assert facts(path, "modified", patch(line))["test_skips_added"] == 1


def test_a_unicode_line_separator_does_not_create_a_diff_line():
    # Guard code review L1: " " inside a string must not become a fabricated "+" line.
    line = "+x = ' +@pytest.mark.skip'"
    assert "test_skips_added" not in facts("tests/test_x.py", "modified", patch(line))


def test_skipped_js_tests_still_count_as_added_tests():
    # Guard code review M2: two running and two skipped tests are four added test cases.
    lines = [
        "+  it('a', () => {})",
        "+  it('b', () => {})",
        "+  it.skip('c', () => {})",
        "+  xit('d', () => {})",
    ]
    result = facts("src/parser.test.ts", "added", patch(*lines))
    assert (result["test_cases_added"], result["test_skips_added"]) == (4, 2)


def test_large_patches_stay_fast():
    # Guard code review M3: a minified one-line config and a 20,000-line workflow hunk.
    started = time.monotonic()
    facts("jest.config.js", "modified", patch("+" + "lines: a " * 20_000))
    hunk = [f"+      - run: pytest -k t{n}" if n % 2 else f"+      echo {n}" for n in range(20_000)]
    facts(".github/workflows/ci.yml", "modified", patch(*hunk))
    assert time.monotonic() - started < 3.0
