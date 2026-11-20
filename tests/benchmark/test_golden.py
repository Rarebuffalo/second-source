"""Tests for golden-set scoring (benchmark/golden.py)."""

import json

import pytest

from second_source.benchmark.golden import (
    DEV,
    TEST,
    GoldenCase,
    Metrics,
    RuleMetrics,
    in_split,
    load_cases,
    merge_labels,
    rules_report,
    score,
    score_rules,
    select_trusted,
    split_for,
)


def test_split_is_deterministic_and_roughly_even():
    urls = [f"https://github.com/o/r/pull/{n}" for n in range(400)]
    splits = [split_for(url) for url in urls]
    assert splits == [split_for(url) for url in urls]
    assert 150 < splits.count(DEV) < 250
    assert set(splits) == {DEV, TEST}


def test_metrics_handle_empty_counts():
    empty = Metrics()
    assert empty.precision == 1.0 and empty.recall == 1.0 and empty.support == 0
    m = Metrics(true_positive=3, false_positive=1, false_negative=2)
    assert m.precision == 0.75 and m.recall == 0.6 and m.support == 5


def test_score_counts_per_type_as_multisets():
    cases = [
        GoldenCase("hit", "All tests pass.", ("TESTS_PASS",)),
        GoldenCase("miss", "Nothing here.", ("LINT_OK",)),
        GoldenCase("extra", "Build succeeds.", ()),
        GoldenCase("two issues", "Fixes #1. Fixes #2.", ("FIXES_ISSUE", "FIXES_ISSUE")),
    ]
    result = score(cases)
    assert result.per_type["TESTS_PASS"].true_positive == 1
    assert result.per_type["LINT_OK"].false_negative == 1
    assert result.per_type["BUILD_OK"].false_positive == 1
    assert result.per_type["FIXES_ISSUE"].true_positive == 2
    assert len(result.mistakes) == 2
    assert "LINT_OK" in result.report() and "Mismatched cases" in result.report()


def test_load_cases_and_split(tmp_path):
    path = tmp_path / "cases.jsonl"
    records = [
        {"url": f"https://github.com/o/r/pull/{n}", "agent": "codex", "body": "x", "expected": []}
        for n in range(10)
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n\n")
    cases = load_cases(path)
    assert len(cases) == 10 and cases[0].agent == "codex"
    assert len(in_split(cases, DEV)) + len(in_split(cases, TEST)) == 10


def test_merge_labels_agreement_adjudication_and_unresolved():
    rows = [{"url": u, "agent": "codex", "body": "b"} for u in ("u1", "u2", "u3")]
    a = {"u1": {"expected": ["TESTS_PASS"]}, "u2": {"expected": []}, "u3": {"expected": []}}
    b = {
        "u1": {"expected": ["TESTS_PASS"]},
        "u2": {"expected": ["LINT_OK"]},
        "u3": {"expected": ["X"]},
    }
    adjudications = {"u2": {"expected": ["LINT_OK"], "reason": "lint stated"}}
    result = merge_labels(rows, a, b, adjudications)
    assert (result.exact_agreement, result.total) == (1, 3)
    assert [r["url"] for r in result.records] == ["u1", "u2"]
    assert result.records[1]["adjudication"] == "lint stated"
    assert [r["url"] for r in result.unresolved] == ["u3"]
    with pytest.raises(ValueError):
        merge_labels(rows, {"u1": a["u1"]}, b, {})


def test_score_rules_judges_each_rule_on_its_own():
    cases = [
        GoldenCase("a", "All new tests pass.", ("TESTS_PASS",)),
        GoldenCase("b", "All new tests pass.", ()),  # labelled as no claim: both rules wrong
        GoldenCase("c", "Fixes #4.", ("FIXES_ISSUE",)),
    ]
    rules = score_rules(cases)
    plain = rules[("TESTS_PASS", "tests_pass.plain")]
    assert (plain.correct, plain.wrong) == (1, 1)
    assert rules[("TESTS_PASS", "tests_pass.quantified")].precision == 0.5
    issue = rules[("FIXES_ISSUE", "fixes_issue.closing_ref")]
    assert issue.precision == 1.0
    assert 0 < issue.precision_low < 1  # one hit proves little
    assert "tests_pass.plain" in rules_report(rules)


def test_command_rules_are_scored_per_claim_type():
    cases = [GoldenCase("a", "- ✅ `pytest -q`\n- ✅ `ruff check .`", ("TESTS_PASS",))]
    rules = score_rules(cases)
    assert rules[("TESTS_PASS", "command.marked")].correct == 1
    assert rules[("LINT_OK", "command.marked")].wrong == 1


def test_select_trusted_needs_enough_hits_and_precision():
    metrics = {
        ("TESTS_PASS", "a"): RuleMetrics("TESTS_PASS", correct=10, wrong=0),
        ("TESTS_PASS", "b"): RuleMetrics("TESTS_PASS", correct=3, wrong=0),  # too few hits
        ("LINT_OK", "c"): RuleMetrics("LINT_OK", correct=9, wrong=1),  # 0.90 < 0.95
    }
    chosen = select_trusted(metrics, min_hits=5, min_precision=0.95)
    assert [(c["type"], c["rule"]) for c in chosen] == [("TESTS_PASS", "a")]
