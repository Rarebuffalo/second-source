"""Tests for integrity facts from unified diffs (packs/coding/diff_facts.py)."""

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
    switched_off = patch("+      informational: true")
    assert facts("codecov.yml", "modified", switched_off)["coverage_gate_lowered"] is True


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
