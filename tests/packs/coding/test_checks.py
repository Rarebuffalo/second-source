"""Scenario tests for the coding pack's checks, run through the real engine.

Evidence is built exactly as the evidence contract (docs/packs/coding.md) describes, so these
tests also pin down what a source must provide.
"""

from datetime import UTC, datetime, timedelta

import pytest

from second_source.core.engine import Engine
from second_source.core.model import Claim, Episode, Evidence, Result
from second_source.packs.coding import load
from second_source.packs.coding.extract import (
    BUILD_OK,
    DEPLOYED,
    DOCS_UPDATED,
    FIXES_ISSUE,
    LINT_OK,
    TESTS_ADDED,
    TESTS_PASS,
    merge_claim,
)

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
SHA = "abc123"
EPISODE = Episode(
    id="octo/app#7",
    kind="pull_request",
    agent="codex",
    opened_at=T0,
    attributes={"repo": "octo/app", "number": 7},
)


@pytest.fixture(scope="module")
def engine():
    return Engine.from_packs([load()])


# ---- builders following the evidence contract -----------------------------------------------


def claim(claim_type, **attributes):
    return Claim(
        id=f"c-{claim_type}",
        episode_id=EPISODE.id,
        type=claim_type,
        made_at=T0,
        attributes=attributes,
    )


def check_result(name, conclusion="success", *, status="completed", ref=SHA, run_id=1):
    """A CI job result keyed as the contract requires: by commit and job name, with the
    run id as the version, so only the newest run of a job survives de-duplication."""
    attributes = {"ref": ref, "name": name, "status": status, "source": "check_run"}
    if conclusion is not None:
        attributes["conclusion"] = conclusion
    return Evidence(
        "check_result", f"{ref}/{name}", T0 + timedelta(minutes=10), attributes, version=run_id
    )


def changed_file(path, status="modified", additions=5):
    return Evidence(
        "changed_file",
        path,
        T0,
        {"path": path, "status": status, "additions": additions, "deletions": 0},
    )


def pr_state(*, merged, state="closed", merged_at=T0 + timedelta(hours=1), merge_sha="m1"):
    attributes = {"state": state, "merged": merged}
    if merged:
        attributes.update(merged_at=merged_at, merge_sha=merge_sha)
    return Evidence("pr_state", "7", T0 + timedelta(hours=2), attributes)


def issue_state(
    number=12,
    state="closed",
    repo="octo/app",
    reopened_at=None,
    closed_at=T0 + timedelta(hours=1, seconds=5),  # just after the default merge time
):
    attributes = {"repo": repo, "number": number, "state": state}
    if state == "closed" and closed_at:
        attributes["closed_at"] = closed_at
    if reopened_at:
        attributes["last_reopened_at"] = reopened_at
    return Evidence("issue_state", f"{repo}#{number}", T0 + timedelta(hours=2), attributes)


def verdict(engine, the_claim, evidence, *, hours=1, stale=frozenset()):
    (result,) = engine.verify(
        EPISODE, [the_claim], evidence, now=T0 + timedelta(hours=hours), stale=stale
    )
    return result


# ---- tests pass / build / lint ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("evidence", "hours", "expected", "reason"),
    [
        (
            [check_result("pytest"), check_result("lint", "failure")],
            1,
            Result.VERIFIED,
            "ALL_MATCHING_CI_JOBS_PASSED",
        ),  # a failing non-test check does not matter here
        ([check_result("pytest", "failure")], 1, Result.CONTRADICTED, "CI_JOB_FAILED"),
        ([check_result("pytest", "timed_out")], 1, Result.CONTRADICTED, "CI_JOB_FAILED"),
        ([check_result("pytest", None, status="in_progress")], 1, Result.PENDING, "AWAITING_CI"),
        (
            [check_result("pytest", None, status="in_progress")],
            12,
            Result.UNVERIFIABLE,
            "CI_TIMEOUT",
        ),
        ([], 1, Result.PENDING, "AWAITING_CI"),
        ([check_result("build")], 12, Result.UNVERIFIABLE, "NO_MATCHING_CI_JOBS"),
        ([check_result("pytest", "skipped")], 12, Result.UNVERIFIABLE, "NO_CI_RESULTS"),
        ([check_result("pytest", "cancelled")], 12, Result.UNVERIFIABLE, "NO_CI_RESULTS"),
        ([check_result("pytest", ref="other")], 12, Result.UNVERIFIABLE, "NO_MATCHING_CI_JOBS"),
    ],
    ids=[
        "pass",
        "fail",
        "timeout",
        "running",
        "ci-timeout",
        "no-ci-yet",
        "no-test-jobs",
        "skipped",
        "cancelled",
        "other-commit",
    ],
)
def test_tests_pass(engine, evidence, hours, expected, reason):
    result = verdict(engine, claim(TESTS_PASS, ref=SHA), evidence, hours=hours)
    assert (result.result, result.reasons) == (expected, (reason,))


def test_latest_rerun_wins(engine):
    # Review finding: a GitHub re-run gets a new run id. Keyed by commit and job name with the
    # run id as version, the newer passing run replaces the older failed one.
    evidence = [
        check_result("pytest", "failure", run_id=100),
        check_result("pytest", "success", run_id=205),
    ]
    assert verdict(engine, claim(TESTS_PASS, ref=SHA), evidence).result is Result.VERIFIED


def test_newest_run_wins_even_when_it_fails(engine):
    evidence = [
        check_result("pytest", "success", run_id=100),
        check_result("pytest", "failure", run_id=205),
    ]
    assert verdict(engine, claim(TESTS_PASS, ref=SHA), evidence).result is Result.CONTRADICTED


@pytest.mark.parametrize("claim_type", [TESTS_PASS, BUILD_OK, LINT_OK])
def test_local_claim_with_failing_ci_is_not_contradicted(engine, claim_type):
    job = {TESTS_PASS: "pytest", BUILD_OK: "build", LINT_OK: "ruff"}[claim_type]
    local = claim(claim_type, ref=SHA, scope="local")
    result = verdict(engine, local, [check_result(job, "failure")])
    assert (result.result, result.reasons) == (Result.UNVERIFIABLE, ("CI_FAILED_LOCAL_CLAIM",))


def test_local_claim_with_passing_ci_is_verified(engine):
    local = claim(TESTS_PASS, ref=SHA, scope="local")
    assert verdict(engine, local, [check_result("pytest")]).result is Result.VERIFIED


def test_missing_commit_reference_is_unverifiable(engine):
    result = verdict(engine, claim(TESTS_PASS), [check_result("pytest")])
    assert result.reasons == ("NO_COMMIT_REFERENCE",)


def test_stale_checks_are_unverifiable(engine):
    result = verdict(engine, claim(TESTS_PASS, ref=SHA), [], stale=frozenset({"check_result"}))
    assert result.reasons == ("SOURCE_STALE",)


@pytest.mark.parametrize(
    ("claim_type", "job", "ok_reason", "fail_reason"),
    [
        (BUILD_OK, "build (ubuntu)", "ALL_MATCHING_CI_JOBS_PASSED", "CI_JOB_FAILED"),
        (LINT_OK, "eslint", "ALL_MATCHING_CI_JOBS_PASSED", "CI_JOB_FAILED"),
    ],
)
def test_build_and_lint(engine, claim_type, job, ok_reason, fail_reason):
    the_claim = claim(claim_type, ref=SHA)
    assert verdict(engine, the_claim, [check_result(job)]).reasons == (ok_reason,)
    assert verdict(engine, the_claim, [check_result(job, "failure")]).reasons == (fail_reason,)


# ---- files: tests added, docs updated --------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "expected", "reason"),
    [
        (
            [changed_file("src/a.py"), changed_file("tests/test_a.py", "added")],
            Result.VERIFIED,
            "TEST_FILES_CHANGED",
        ),
        ([changed_file("src/a.py")], Result.CONTRADICTED, "NO_TEST_FILES_CHANGED"),
        (
            [changed_file("tests/test_a.py", "removed")],
            Result.CONTRADICTED,
            "NO_TEST_FILES_CHANGED",
        ),  # deleting tests is not adding them
        (
            [changed_file("tests/test_a.py", additions=0)],
            Result.CONTRADICTED,
            "NO_TEST_FILES_CHANGED",
        ),
        ([], Result.UNVERIFIABLE, "NO_FILE_LIST"),
    ],
)
def test_tests_added(engine, files, expected, reason):
    result = verdict(engine, claim(TESTS_ADDED), files)
    assert (result.result, result.reasons) == (expected, (reason,))


def test_docs_updated(engine):
    assert (
        verdict(engine, claim(DOCS_UPDATED), [changed_file("README.md")]).result is Result.VERIFIED
    )
    assert (
        verdict(engine, claim(DOCS_UPDATED), [changed_file("src/a.py")]).result
        is Result.CONTRADICTED
    )


# ---- fixes issue (L4 now, L5 over time) ------------------------------------------------------


def fixes_claim(issue=12, repo=""):
    return claim(FIXES_ISSUE, issue=issue, repo=repo)


def runs_by_check(result):
    return {run.check_id: (run.result, run.reason) for run in result.check_runs}


def test_fixes_issue_closed_and_stays_closed(engine):
    evidence = [pr_state(merged=True), issue_state()]
    result = verdict(engine, fixes_claim(), evidence, hours=24 * 40)
    assert result.result is Result.VERIFIED
    assert runs_by_check(result)["coding.fixes_issue_durable"] == (
        Result.VERIFIED,
        "ISSUE_STAYED_CLOSED",
    )


def test_fixes_issue_in_window_is_pending(engine):
    result = verdict(engine, fixes_claim(), [pr_state(merged=True), issue_state()], hours=24)
    assert runs_by_check(result)["coding.fixes_issue"] == (Result.VERIFIED, "ISSUE_CLOSED_BY_MERGE")
    assert result.result is Result.PENDING  # durability still being watched


def test_fixes_issue_still_open_after_merge(engine):
    result = verdict(engine, fixes_claim(), [pr_state(merged=True), issue_state(state="open")])
    assert result.result is Result.CONTRADICTED
    assert "ISSUE_STILL_OPEN" in result.reasons


def test_fixes_issue_reopened_after_merge(engine):
    reopened = issue_state(state="closed", reopened_at=T0 + timedelta(days=3))
    result = verdict(engine, fixes_claim(), [pr_state(merged=True), reopened], hours=24 * 5)
    assert "ISSUE_REOPENED" in result.reasons


def test_fixes_issue_not_merged_yet_is_pending(engine):
    result = verdict(
        engine, fixes_claim(), [pr_state(merged=False, state="open"), issue_state(state="open")]
    )
    assert runs_by_check(result)["coding.fixes_issue"] == (Result.PENDING, "NOT_MERGED_YET")


def test_fixes_issue_pr_closed_unmerged_is_unverifiable(engine):
    result = verdict(engine, fixes_claim(), [pr_state(merged=False), issue_state(state="open")])
    assert result.result is Result.UNVERIFIABLE


def test_cross_repo_issue_reference(engine):
    other = issue_state(number=4, repo="octo/lib")
    same_number_here = issue_state(number=4, repo="octo/app", state="open")
    result = verdict(
        engine,
        fixes_claim(issue=4, repo="octo/lib"),
        [pr_state(merged=True), other, same_number_here],
        hours=24 * 40,
    )
    assert result.result is Result.VERIFIED


def test_issue_closed_before_the_merge_proves_nothing(engine):
    # Review finding: an issue closed long before the merge must not count as fixed by it.
    already_closed = issue_state(closed_at=T0 - timedelta(days=30))
    result = verdict(engine, fixes_claim(), [pr_state(merged=True), already_closed])
    assert runs_by_check(result)["coding.fixes_issue"] == (
        Result.UNVERIFIABLE,
        "ISSUE_ALREADY_CLOSED",
    )


def test_issue_closed_moments_before_merge_timestamp_still_counts(engine):
    # GitHub timestamps can differ by seconds; the tolerance absorbs that.
    nearly = issue_state(closed_at=T0 + timedelta(minutes=58))  # merge is at T0 + 1h
    result = verdict(engine, fixes_claim(), [pr_state(merged=True), nearly])
    assert runs_by_check(result)["coding.fixes_issue"][0] is Result.VERIFIED


def test_repo_names_match_case_insensitively(engine):
    # Review finding: extraction lower-cases "Octo/Lib"; sources report canonical case.
    canonical_case = issue_state(number=4, repo="Octo/Lib")
    result = verdict(
        engine,
        fixes_claim(issue=4, repo="octo/lib"),
        [pr_state(merged=True), canonical_case],
        hours=24 * 40,
    )
    assert result.result is Result.VERIFIED


def test_inaccessible_issue_is_unverifiable(engine):
    result = verdict(engine, fixes_claim(), [pr_state(merged=True)])
    assert runs_by_check(result)["coding.fixes_issue"] == (
        Result.UNVERIFIABLE,
        "ISSUE_NOT_ACCESSIBLE",
    )


# ---- deployed --------------------------------------------------------------------------------


def deployment(state, environment="staging", ref=SHA, dep_id="d1"):
    return Evidence(
        "deployment_status",
        dep_id,
        T0 + timedelta(minutes=30),
        {"ref": ref, "environment": environment, "state": state, "at": T0},
    )


@pytest.mark.parametrize(
    ("evidence", "hours", "expected"),
    [
        ([deployment("success")], 1, Result.VERIFIED),
        ([deployment("success", environment="staging-eu")], 1, Result.VERIFIED),
        ([deployment("failure")], 1, Result.CONTRADICTED),
        ([deployment("in_progress")], 1, Result.PENDING),
        ([deployment("success", environment="production")], 1, Result.UNVERIFIABLE),
        ([deployment("success", environment="Staging-EU")], 1, Result.VERIFIED),
        ([], 1, Result.UNVERIFIABLE),  # repo without GitHub deployments: never an accusation
    ],
)
def test_deployed(engine, evidence, hours, expected):
    result = verdict(engine, claim(DEPLOYED, ref=SHA, environment="staging"), evidence, hours=hours)
    assert result.result is expected


def test_deployed_aliases_apply_to_evidence_too(engine):
    # Review finding: "Deployed to prod" must match an environment literally named "prod".
    the_claim = claim(DEPLOYED, ref=SHA, environment="production")  # extraction normalised it
    result = verdict(engine, the_claim, [deployment("success", environment="prod")])
    assert result.result is Result.VERIFIED


# ---- merge holds (not reverted) --------------------------------------------------------------


def revert(reverted_sha="m1", sha="r1"):
    return Evidence("revert", sha, T0 + timedelta(days=2), {"reverted_sha": reverted_sha, "at": T0})


@pytest.mark.parametrize(
    ("evidence", "days", "expected", "reason"),
    [
        ([], 3, Result.PENDING, "IN_WINDOW"),
        ([], 31, Result.VERIFIED, "NOT_REVERTED"),
        ([revert()], 3, Result.CONTRADICTED, "REVERTED"),
        ([revert(reverted_sha="other")], 31, Result.VERIFIED, "NOT_REVERTED"),
    ],
)
def test_not_reverted(engine, evidence, days, expected, reason):
    the_claim = merge_claim(EPISODE.id, T0, "m1")
    result = verdict(engine, the_claim, evidence, hours=24 * days)
    assert (result.result, result.reasons) == (expected, (reason,))
