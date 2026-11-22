"""Tests for benchmark statistics (benchmark/stats.py)."""

import pytest

from second_source.benchmark.stats import (
    ALL,
    Cell,
    combine,
    outcome_of,
    summarize,
    to_markdown,
    wilson_interval,
)


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


def test_cell_counts_scoped_ci_failures_separately():
    cell = Cell()
    for result, reasons in [
        ("VERIFIED", []),
        ("CONTRADICTED", ["CI_JOB_FAILED"]),
        ("UNVERIFIABLE", ["CI_FAILED_LOCAL_CLAIM"]),
        ("UNVERIFIABLE", ["NO_MATCHING_CI_JOBS"]),
        ("PENDING", ["AWAITING_CI"]),
    ]:
        cell.add(outcome_of(result, reasons))
    assert (cell.verified, cell.contradicted, cell.scoped_ci_failed) == (1, 1, 1)
    assert (cell.unverifiable, cell.pending, cell.total) == (1, 1, 5)
    assert cell.false_rate == 0.5  # scoped CI failures never count as false
    assert cell.coverage == 0.4


def test_a_prs_claims_of_one_type_combine_into_one_outcome():
    assert combine(["VERIFIED", "CONTRADICTED", "UNVERIFIABLE"]) == "CONTRADICTED"
    assert combine(["UNVERIFIABLE", "VERIFIED"]) == "VERIFIED"
    assert combine(["UNVERIFIABLE", "SCOPED_CI_FAILED"]) == "SCOPED_CI_FAILED"
    assert outcome_of("UNVERIFIABLE", ["CI_FAILED_SUBSET_CLAIM"]) == "SCOPED_CI_FAILED"


def test_empty_cell_has_no_rates():
    data = Cell().to_dict()
    assert data["false_rate"] is None and data["coverage"] is None
    assert data["false_rate_ci95"] is None


def claim(kind, result, *reasons):
    return {"type": kind, "result": result, "reasons": list(reasons)}


RECORDS = [
    {
        "url": "https://github.com/a/one/pull/1",
        "dataset_agent": "codex",
        "report": {
            "agent": {"name": "codex"},
            "claims": [claim("TESTS_PASS", "VERIFIED"), claim("MERGE_HOLDS", "VERIFIED")],
        },
    },
    {
        "url": "https://github.com/b/two/pull/2",
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
    assert tests_pass["repos"] == 2
    assert codex["claims"]["MERGE_HOLDS"]["prs"] == 1
    assert codex["claims"][ALL]["prs"] == 2  # MERGE_HOLDS is not a claim the agent wrote
    assert codex["claims"][ALL]["verified"] == 1
    assert summary["agents"]["devin"]["claims"] == {}


def test_many_claims_on_one_pr_count_once():
    many = [claim("TESTS_PASS", "UNVERIFIABLE", "NO_CI_RECORDS")] * 25
    record = {
        "url": "https://github.com/a/one/pull/1",
        "dataset_agent": "codex",
        "report": {"agent": None, "claims": [*many, claim("TESTS_PASS", "VERIFIED")]},
    }
    tests_pass = summarize([record])["agents"]["codex"]["claims"]["TESTS_PASS"]
    assert (tests_pass["prs"], tests_pass["verified"], tests_pass["unverifiable"]) == (1, 1, 0)


def test_markdown_lists_every_agent_and_type():
    text = to_markdown(summarize(RECORDS))
    assert "## codex" in text and "## devin" in text
    assert "| TESTS_PASS | 2 | 2 | 1 | 1 | 0 | 0 | 100% | 50% (" in text
    assert "Without guards" in text
    assert "never counted as false" in text


def test_low_confidence_claims_are_counted_separately():
    low = {
        **claim("BUILD_OK", "CONTRADICTED", "CI_JOB_FAILED"),
        "attributes": {"confidence": "low"},
    }
    high = {**claim("TESTS_PASS", "VERIFIED"), "attributes": {"confidence": "high"}}
    implicit = claim("MERGE_HOLDS", "VERIFIED")  # no confidence attribute: always counted
    record = {
        "url": "u",
        "dataset_agent": "codex",
        "report": {"agent": None, "claims": [low, high, implicit]},
    }
    codex = summarize([record])["agents"]["codex"]
    assert codex["low_confidence_claims"] == 1
    assert "BUILD_OK" not in codex["claims"]
    assert codex["claims"][ALL]["prs"] == 1
    assert "1 low-confidence claims not counted" in to_markdown(summarize([record]))


def test_testreceipt_comparable_cell_counts_body_claims_by_year():
    def body_claim(result, **attributes):
        return {
            "type": "TESTS_PASS",
            "result": result,
            "reasons": [],
            "origin": "body",
            "attributes": {"confidence": "high", **attributes},
        }

    def record(n, year, *claims):
        report = {"agent": None, "pull_request": {"opened_at": f"{year}-05-01T00:00:00+00:00"}}
        return {
            "url": f"https://github.com/o/r{n}/pull/1",
            "dataset_agent": "codex",
            "report": {**report, "claims": list(claims)},
        }

    records = [
        record(1, 2025, body_claim("VERIFIED")),
        record(2, 2025, body_claim("CONTRADICTED")),
        record(3, 2025, body_claim("CONTRADICTED", scope="subset")),  # scoped: not comparable
        record(4, 2026, body_claim("UNVERIFIABLE")),  # undecided: not counted
        record(5, 2026, body_claim("VERIFIED")),
    ]
    cells = summarize(records)["testreceipt_comparable"]
    assert (cells["2025"]["checkable"], cells["2025"]["contradicted"]) == (2, 1)
    assert (cells["2026"]["checkable"], cells["2026"]["contradicted"]) == (1, 0)
    assert "Comparable to testreceipt" in to_markdown(summarize(records))


def test_withdrawn_verifications_are_counted_apart_from_the_false_rate():
    def record(n, claim):
        return {
            "url": f"https://github.com/o/r{n}/pull/1",
            "dataset_agent": "codex",
            "tool_version": "0.1.0",
            "pack_version": "0.4.0" if n else "0.3.0",
            "report": {"agent": None, "claims": [claim]},
        }

    withdrawn = {
        **claim("TESTS_PASS", "UNVERIFIABLE", "PR_SKIPS_TESTS"),
        "withdrawn": ["PR_SKIPS_TESTS"],
    }
    summary = summarize(
        [
            record(0, claim("TESTS_PASS", "VERIFIED")),
            record(1, withdrawn),
            record(2, claim("TESTS_PASS", "CONTRADICTED")),
        ]
    )
    cell = summary["agents"]["codex"]["claims"]["TESTS_PASS"]
    assert (cell["withdrawn_by_integrity_guard"], cell["checkable"], cell["false_rate"]) == (
        1,
        2,
        0.5,
    )
    assert len(summary["code_versions"]) == 2
    assert "Mixed code versions" in to_markdown(summary)
