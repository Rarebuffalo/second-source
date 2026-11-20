"""Tests for benchmark statistics (benchmark/stats.py)."""

import pytest

from second_source.benchmark.stats import ALL, Cell, summarize, to_markdown, wilson_interval


def test_wilson_matches_published_values():
    low, high = wilson_interval(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-4) and high == pytest.approx(0.7634, abs=1e-4)
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and high == pytest.approx(0.2775, abs=1e-4)


def test_wilson_edge_cases():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    low, high = wilson_interval(10, 10)
    assert high == 1.0 and 0.69 < low < 0.73
    with pytest.raises(ValueError):
        wilson_interval(3, 2)


def test_cell_counts_local_ci_failures_separately():
    cell = Cell()
    cell.add("VERIFIED", [])
    cell.add("CONTRADICTED", ["CI_JOB_FAILED"])
    cell.add("UNVERIFIABLE", ["CI_FAILED_LOCAL_CLAIM"])
    cell.add("UNVERIFIABLE", ["NO_MATCHING_CI_JOBS"])
    cell.add("PENDING", ["AWAITING_CI"])
    assert (cell.verified, cell.contradicted, cell.local_ci_failed) == (1, 1, 1)
    assert (cell.unverifiable, cell.pending, cell.total) == (1, 1, 5)
    assert cell.false_rate == 0.5  # local CI failures never count as false
    assert cell.coverage == 0.4


def test_empty_cell_has_no_rates():
    data = Cell().to_dict()
    assert data["false_rate"] is None and data["coverage"] is None
    assert data["false_rate_ci95"] is None


def claim(kind, result, *reasons):
    return {"type": kind, "result": result, "reasons": list(reasons)}


RECORDS = [
    {
        "url": "u1",
        "dataset_agent": "codex",
        "report": {
            "agent": {"name": "codex"},
            "claims": [claim("TESTS_PASS", "VERIFIED"), claim("MERGE_HOLDS", "VERIFIED")],
        },
    },
    {
        "url": "u2",
        "dataset_agent": "codex",
        "report": {"agent": None, "claims": [claim("TESTS_PASS", "CONTRADICTED", "CI_JOB_FAILED")]},
    },
    {"url": "u3", "dataset_agent": "codex", "error": "not_found"},
    {"url": "u4", "dataset_agent": "devin", "report": {"agent": None, "claims": []}},
]


def test_summarize_per_agent_and_type():
    summary = summarize(RECORDS)
    codex = summary["agents"]["codex"]
    assert (codex["prs"], codex["errors"], codex["detection_agreement"]) == (2, 1, 0.5)
    tests_pass = codex["claims"]["TESTS_PASS"]
    assert tests_pass["false_rate"] == 0.5 and tests_pass["checkable"] == 2
    assert codex["claims"][ALL]["claims"] == 3
    assert summary["agents"]["devin"]["claims"] == {}


def test_markdown_lists_every_agent_and_type():
    text = to_markdown(summarize(RECORDS))
    assert "## codex" in text and "## devin" in text
    assert "| TESTS_PASS | 2 | 1 | 1 | 0 | 100% | 50% (" in text
    assert "never counted as false" in text
