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


def claim(claim_type, origin="body", **attributes):
    return Claim(
        id=f"c-{claim_type}",
        episode_id=EPISODE.id,
        type=claim_type,
        made_at=T0,
        attributes=attributes,
        origin=origin,
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


def workflow(name, status="completed", conclusion="success", *, ref=SHA, created=T0, run_id=1):
    """A GitHub Actions workflow run on a commit (whether CI is still coming)."""
    attributes = {"ref": ref, "name": name, "status": status, "created_at": created}
    if conclusion is not None:
        attributes["conclusion"] = conclusion
    return Evidence("ci_workflow_run", f"{ref}/workflow-run:{run_id}", T0, attributes)


def changed_file(path, status="modified", additions=5):
    return Evidence(
        "changed_file",
        path,
        T0,
        {"path": path, "status": status, "additions": additions, "deletions": 0},
    )


def pr_state(
    *, merged, state="closed", merged_at=T0 + timedelta(hours=1), merge_sha="m1", base="main"
):
    attributes = {"state": state, "merged": merged, "base_ref": base, "default_branch": "main"}
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
        # A timeout is usually the runner, not the code: neither a pass nor a failure.
        ([check_result("pytest", "timed_out")], 1, Result.UNVERIFIABLE, "NO_CI_RESULTS"),
        ([check_result("pytest", None, status="in_progress")], 1, Result.PENDING, "AWAITING_CI"),
        (
            [check_result("pytest", None, status="in_progress")],
            12,
            Result.UNVERIFIABLE,
            "CI_TIMEOUT",
        ),
        ([], 0.1, Result.PENDING, "AWAITING_CI"),  # within the grace period for CI to appear
        ([], 1, Result.UNVERIFIABLE, "NO_CI_RECORDS"),  # no CI showed up: not hours of waiting
        # CI finished without a matching job: decided now, not after the 6-hour timeout.
        ([check_result("build")], 1, Result.UNVERIFIABLE, "NO_MATCHING_CI_JOBS"),
        # Other CI still running: a matching job may still appear in it.
        ([check_result("build", None, status="queued")], 1, Result.PENDING, "AWAITING_CI"),
        ([check_result("build")], 12, Result.UNVERIFIABLE, "NO_MATCHING_CI_JOBS"),
        ([check_result("pytest", "skipped")], 12, Result.UNVERIFIABLE, "NO_CI_RESULTS"),
        ([check_result("pytest", "cancelled")], 12, Result.UNVERIFIABLE, "NO_CI_RESULTS"),
        # CI ran only on another commit: nothing on the claimed one.
        ([check_result("pytest", ref="other")], 12, Result.UNVERIFIABLE, "NO_CI_RECORDS"),
        # Long after the claim and no CI data at all (for example, deleted workflow runs).
        ([], 12, Result.UNVERIFIABLE, "NO_CI_RECORDS"),
    ],
    ids=[
        "pass",
        "fail",
        "timeout",
        "running",
        "ci-timeout",
        "no-ci-yet",
        "no-ci-after-grace",
        "no-test-job-finished",
        "other-ci-running",
        "no-test-jobs",
        "skipped",
        "cancelled",
        "other-commit",
        "no-ci-records",
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


@pytest.mark.parametrize("claim_type", [TESTS_PASS, LINT_OK])
def test_subset_claim_with_failing_ci_is_not_contradicted(engine, claim_type):
    # "Targeted tests pass" or "biome lint passes" may say nothing about the job that failed.
    job = {TESTS_PASS: "e2e tests", LINT_OK: "eslint"}[claim_type]
    subset = claim(claim_type, ref=SHA, scope="subset")
    result = verdict(engine, subset, [check_result(job, "failure")])
    assert (result.result, result.reasons) == (Result.UNVERIFIABLE, ("CI_FAILED_SUBSET_CLAIM",))


def test_subset_claim_with_passing_ci_is_verified(engine):
    subset = claim(TESTS_PASS, ref=SHA, scope="subset")
    assert verdict(engine, subset, [check_result("pytest")]).result is Result.VERIFIED


# ---- body claims judged on two commits (PR creation and final head) -----------------------

FINAL = "f" * 40


@pytest.mark.parametrize(
    ("evidence", "expected", "reason"),
    [
        # Fails when the PR opened, passes on the final head: which one the text means is unknown.
        (
            [check_result("pytest", "failure"), check_result("pytest", ref=FINAL)],
            Result.UNVERIFIABLE,
            "CLAIM_COMMIT_AMBIGUOUS",
        ),
        # Copilot's empty "Initial plan" commit passes, the real work fails.
        (
            [check_result("pytest"), check_result("pytest", "failure", ref=FINAL)],
            Result.UNVERIFIABLE,
            "CLAIM_COMMIT_AMBIGUOUS",
        ),
        (
            [check_result("pytest", "failure"), check_result("pytest", "failure", ref=FINAL)],
            Result.CONTRADICTED,
            "CI_JOB_FAILED",
        ),
        ([check_result("pytest", ref=FINAL)], Result.VERIFIED, "ALL_MATCHING_CI_JOBS_PASSED"),
        # A pass only on the opening commit does not verify the final change.
        ([check_result("pytest")], Result.UNVERIFIABLE, "NO_CI_RESULTS"),
    ],
    ids=["fail-then-pass", "pass-then-fail", "fail-both", "pass-final", "pass-start-only"],
)
def test_body_claims_with_a_final_head(engine, evidence, expected, reason):
    two_commits = claim(TESTS_PASS, ref=SHA, final_ref=FINAL)
    result = verdict(engine, two_commits, evidence, hours=12)
    assert (result.result, result.reasons) == (expected, (reason,))


def test_a_push_to_an_old_pr_waits_for_the_final_heads_ci(engine):
    # Code review M4: the PR opened long ago (made_at = T0) but the final head was pushed
    # recently; its CI has not reported, so the claim waits instead of being judged on `ref`.
    pushed = claim(TESTS_PASS, ref=SHA, final_ref=FINAL, final_at=T0 + timedelta(hours=20))
    result = verdict(engine, pushed, [check_result("pytest", "failure")], hours=20.1)
    assert (result.result, result.reasons) == (Result.PENDING, ("AWAITING_CI",))
    # Its CI is queued an hour after the push: still waited for, by the final head's own time.
    queued = [
        check_result("pytest", "failure"),
        check_result("pytest", None, status="queued", ref=FINAL),
    ]
    still = verdict(engine, pushed, queued, hours=21)
    assert (still.result, still.reasons) == (Result.PENDING, ("AWAITING_CI",))
    # No CI ever appears on the final head: after the grace period it is decided, and a
    # failure on the opening commit alone is still not a contradiction.
    later = verdict(engine, pushed, [check_result("pytest", "failure")], hours=21)
    assert (later.result, later.reasons) == (Result.UNVERIFIABLE, ("CLAIM_COMMIT_AMBIGUOUS",))


@pytest.mark.parametrize(
    ("evidence", "hours", "expected", "reason"),
    [
        # A test job passed, but a workflow is still running: a later `needs:` job may match.
        (
            [check_result("pytest"), workflow("CI", "in_progress", None)],
            1,
            Result.PENDING,
            "AWAITING_CI",
        ),
        # Held for approval (Copilot, forks): no check runs yet, so CI has not run.
        ([workflow("CI", "completed", "action_required")], 1, Result.PENDING, "AWAITING_CI"),
        ([workflow("CI", "completed", "action_required")], 7, Result.UNVERIFIABLE, "NO_CI_RECORDS"),
        # Our own workflow running is not CI.
        (
            [check_result("build"), workflow("Second Source", "in_progress", None)],
            1,
            Result.UNVERIFIABLE,
            "NO_MATCHING_CI_JOBS",
        ),
        # A commit made long ago but pushed just now: its CI is waited for from the push.
        (
            [workflow("CI", "queued", None, created=T0 + timedelta(hours=20))],
            21,
            Result.PENDING,
            "AWAITING_CI",
        ),
        # Two runs of one workflow: the finished one does not hide the one still going.
        (
            [check_result("pytest"), workflow("CI"), workflow("CI", "in_progress", None, run_id=2)],
            1,
            Result.PENDING,
            "AWAITING_CI",
        ),
        # Past the window with a workflow unfinished: passing jobs do not verify the claim.
        (
            [check_result("pytest"), workflow("CI", "in_progress", None)],
            7,
            Result.UNVERIFIABLE,
            "CI_TIMEOUT",
        ),
        # A workflow waiting for a deployment approval does not hold a passing claim.
        (
            [check_result("pytest"), workflow("Deploy", "waiting", None)],
            1,
            Result.VERIFIED,
            "ALL_MATCHING_CI_JOBS_PASSED",
        ),
        # Workflows all finished, no matching job: decided at once.
        ([check_result("build"), workflow("CI")], 1, Result.UNVERIFIABLE, "NO_MATCHING_CI_JOBS"),
    ],
    ids=[
        "workflow-running",
        "held-for-approval",
        "held-too-long",
        "own-workflow",
        "pushed-late",
        "two-runs-one-workflow",
        "unfinished-past-window",
        "deploy-approval",
        "all-finished",
    ],
)
def test_waiting_follows_workflow_runs(engine, evidence, hours, expected, reason):
    result = verdict(engine, claim(TESTS_PASS, ref=SHA), evidence, hours=hours)
    assert (result.result, result.reasons) == (expected, (reason,))


def test_other_ci_running_on_the_final_head_is_waited_for(engine):
    two_commits = claim(TESTS_PASS, ref=SHA, final_ref=FINAL)
    other = [check_result("build", None, status="in_progress", ref=FINAL)]
    result = verdict(engine, two_commits, other, hours=1)
    assert (result.result, result.reasons) == (Result.PENDING, ("AWAITING_CI",))


def test_a_pending_commit_status_counts_as_running(engine):
    # Commit statuses (older CI) map "pending" to in_progress.
    pending = Evidence(
        "check_result",
        f"{SHA}/status:ci/jenkins",
        T0,
        {"ref": SHA, "name": "ci/jenkins", "status": "in_progress", "source": "status"},
    )
    result = verdict(engine, claim(TESTS_PASS, ref=SHA), [pending], hours=1)
    assert (result.result, result.reasons) == (Result.PENDING, ("AWAITING_CI",))


def test_our_own_check_run_is_not_ci_evidence(engine):
    for own in ("Second Source", "second-source"):  # the check run, and the workflow's job
        result = verdict(engine, claim(TESTS_PASS, ref=SHA), [check_result(own)], hours=12)
        assert result.reasons == ("NO_CI_RECORDS",)


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
        # Never contradicted: tests can live inside source files (src/a.rs #[cfg(test)]).
        ([changed_file("src/a.py")], Result.UNVERIFIABLE, "NO_TEST_FILES_FOUND"),
        ([changed_file("docs/a.md")], Result.UNVERIFIABLE, "NO_TEST_FILES_FOUND"),
        (
            [changed_file("tests/test_a.py", "removed")],
            Result.UNVERIFIABLE,
            "NO_TEST_FILES_FOUND",
        ),  # deleting tests is not adding them
        (
            [changed_file("tests/test_a.py", additions=0)],
            Result.UNVERIFIABLE,
            "NO_TEST_FILES_FOUND",
        ),
        ([changed_file("src/tests.rs", "added")], Result.VERIFIED, "TEST_FILES_CHANGED"),
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
        verdict(engine, claim(DOCS_UPDATED), [changed_file("man/add_edges.Rd")]).result
        is Result.VERIFIED
    )
    # Never contradicted: docs can live in code (docstrings, XML doc comments).
    missing = verdict(engine, claim(DOCS_UPDATED), [changed_file("src/a.py")])
    assert (missing.result, missing.reasons) == (Result.UNVERIFIABLE, ("NO_DOC_FILES_FOUND",))


# ---- fixes issue (L4 now, L5 over time) ------------------------------------------------------


def fixes_claim(issue=12, repo="", origin="body"):
    return claim(FIXES_ISSUE, origin=origin, issue=issue, repo=repo)


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
    evidence = [pr_state(merged=True), issue_state(state="open")]
    result = verdict(engine, fixes_claim(), evidence, hours=2)
    assert result.result is Result.CONTRADICTED
    assert "ISSUE_STILL_OPEN" in result.reasons


def test_fixes_issue_open_moments_after_the_merge_is_pending(engine):
    # GitHub closes linked issues just after the merge; a check on the merge event can be early.
    evidence = [pr_state(merged=True), issue_state(state="open")]
    result = verdict(engine, fixes_claim(), evidence, hours=1)
    assert runs_by_check(result)["coding.fixes_issue"] == (Result.PENDING, "CLOSE_PENDING")
    # The durability check must agree: the final verdict is pending, never contradicted
    # (review of the stage B fixes: it used to say ISSUE_OPEN and win).
    assert runs_by_check(result)["coding.fixes_issue_durable"] == (Result.PENDING, "CLOSE_PENDING")
    assert result.result is Result.PENDING


@pytest.mark.parametrize(
    ("origin", "repo"), [("title", ""), ("comment", ""), ("body", "other/repo")]
)
def test_fixes_issue_open_where_github_never_auto_closes_is_unverifiable(engine, origin, repo):
    issue = issue_state(state="open", repo=repo or "octo/app")
    result = verdict(
        engine, fixes_claim(origin=origin, repo=repo), [pr_state(merged=True), issue], hours=2
    )
    assert runs_by_check(result)["coding.fixes_issue"] == (Result.UNVERIFIABLE, "NOT_AUTO_CLOSED")
    assert result.result is not Result.CONTRADICTED


def test_fixes_issue_open_after_merge_into_another_branch_is_unverifiable(engine):
    # GitHub only auto-closes linked issues for merges into the default branch.
    evidence = [pr_state(merged=True, base="feature/x"), issue_state(state="open")]
    result = verdict(engine, fixes_claim(), evidence)
    assert runs_by_check(result)["coding.fixes_issue"] == (
        Result.UNVERIFIABLE,
        "NOT_DEFAULT_BRANCH",
    )
    assert result.result is not Result.CONTRADICTED


def test_fixes_issue_reopened_after_merge_and_still_open(engine):
    reopened = issue_state(state="open", reopened_at=T0 + timedelta(days=3))
    result = verdict(engine, fixes_claim(), [pr_state(merged=True), reopened], hours=24 * 5)
    assert result.result is Result.CONTRADICTED
    assert "ISSUE_REOPENED" in result.reasons


@pytest.mark.parametrize(
    ("origin", "repo"), [("title", ""), ("comment", ""), ("body", "other/repo")]
)
def test_a_reopened_issue_github_never_linked_is_not_a_contradiction(engine, origin, repo):
    # Found by the randomised oracle tests: a reference GitHub does not auto-close was never
    # linked to the PR, so its issue being reopened says nothing about this PR.
    issue = issue_state(state="open", repo=repo or "octo/app", reopened_at=T0 + timedelta(days=3))
    evidence = [pr_state(merged=True), issue]
    result = verdict(engine, fixes_claim(repo=repo, origin=origin), evidence, hours=24 * 5)
    assert result.result is not Result.CONTRADICTED


def test_fixes_issue_reopened_then_closed_again_is_unverifiable(engine):
    # Reopened to verify the fix (or fixed by a later PR), then closed: ambiguous, not false.
    reopened = issue_state(state="closed", reopened_at=T0 + timedelta(days=3))
    result = verdict(engine, fixes_claim(), [pr_state(merged=True), reopened], hours=24 * 5)
    assert runs_by_check(result)["coding.fixes_issue_durable"] == (
        Result.UNVERIFIABLE,
        "REOPENED_THEN_CLOSED",
    )
    assert result.result is not Result.CONTRADICTED


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


def deployment(state, environment="staging", ref=SHA, dep_id="d1", complete=True):
    return Evidence(
        "deployment_status",
        dep_id,
        T0 + timedelta(minutes=30),
        {"ref": ref, "environment": environment, "state": state, "at": T0, "complete": complete},
    )


@pytest.mark.parametrize(
    ("evidence", "hours", "expected"),
    [
        ([deployment("success")], 1, Result.VERIFIED),
        ([deployment("success", environment="staging-eu")], 1, Result.VERIFIED),
        ([deployment("failure")], 1, Result.CONTRADICTED),
        # More deployments than the source lists: a success may be among the unseen ones.
        ([deployment("failure", complete=False)], 1, Result.UNVERIFIABLE),
        # A retry is still deploying: wait.
        ([deployment("failure"), deployment("in_progress", dep_id="d2")], 1, Result.PENDING),
        ([deployment("failure"), deployment("in_progress", dep_id="d2")], 5, Result.UNVERIFIABLE),
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


def revert(reverted_sha="m1", sha="r1", undone=False):
    attributes = {"reverted_sha": reverted_sha, "at": T0, "undone": undone}
    return Evidence("revert", sha, T0 + timedelta(days=2), attributes)


@pytest.mark.parametrize(
    ("evidence", "days", "expected", "reason"),
    [
        ([], 3, Result.PENDING, "IN_WINDOW"),
        ([], 31, Result.VERIFIED, "NOT_REVERTED"),
        ([revert()], 3, Result.CONTRADICTED, "REVERTED"),
        # The revert was itself reverted: the change is back, so "held" is ambiguous.
        ([revert(undone=True)], 31, Result.UNVERIFIABLE, "REVERT_UNDONE"),
        ([revert(undone=True), revert(sha="r2")], 31, Result.CONTRADICTED, "REVERTED"),
        ([revert(reverted_sha="other")], 31, Result.VERIFIED, "NOT_REVERTED"),
        # Merged into a feature branch: no branch to watch.
        ([pr_state(merged=True, base="feature/x")], 31, Result.UNVERIFIABLE, "NOT_DEFAULT_BRANCH"),
        ([pr_state(merged=True)], 31, Result.VERIFIED, "NOT_REVERTED"),
    ],
)
def test_not_reverted(engine, evidence, days, expected, reason):
    the_claim = merge_claim(EPISODE.id, T0, "m1")
    result = verdict(engine, the_claim, evidence, hours=24 * days)
    assert (result.result, result.reasons) == (expected, (reason,))


# ---- integrity guards (false VERIFIED) -------------------------------------------------------


def summary(**totals):
    base = {
        "files": 3, "test_skips_net": 0, "test_cases_added": 0, "test_cases_removed": 0,
        "asserts_added": 0, "test_cmd_weakened": 0, "lint_suppressions_added": 0,
        "removed_test_files": 0, "test_source_changed": 0, "code_net": 0, "code_removed": False,
        "coverage_gate_lowered": False, "ci_config_changed": False, "lint_config_changed": False,
        "uninspected": 0,
    }  # fmt: skip
    return Evidence("changed_files_summary", "7", T0 + timedelta(minutes=5), {**base, **totals})


@pytest.mark.parametrize(
    ("totals", "reason"),
    [
        ({"test_skips_net": 2}, "PR_SKIPS_TESTS"),
        ({"test_cmd_weakened": 1}, "PR_WEAKENS_TEST_COMMAND"),
        ({"coverage_gate_lowered": True}, "PR_LOWERS_COVERAGE_GATE"),
    ],
)
def test_tampering_withdraws_a_passing_ci_verification(engine, totals, reason):
    result = verdict(
        engine, claim(TESTS_PASS, ref=SHA), [check_result("pytest"), summary(**totals)]
    )
    assert (result.result, result.reasons) == (Result.UNVERIFIABLE, (reason,))


def test_tampering_never_turns_into_an_accusation(engine):
    # A failing job still contradicts; a guard can only withdraw, so it cannot add or remove that.
    failing = [check_result("pytest", "failure"), summary(test_skips_net=3)]
    assert verdict(engine, claim(TESTS_PASS, ref=SHA), failing).result is Result.CONTRADICTED


def test_a_clean_pr_keeps_its_verification_and_reasons(engine):
    result = verdict(engine, claim(TESTS_PASS, ref=SHA), [check_result("pytest"), summary()])
    assert (result.result, result.reasons) == (Result.VERIFIED, ("ALL_MATCHING_CI_JOBS_PASSED",))
    assert result.caveats == ()


def test_cautions_are_shown_but_never_change_the_result(engine):
    totals = {"ci_config_changed": True, "test_cases_removed": 3, "lint_suppressions_added": 1}
    tests = verdict(engine, claim(TESTS_PASS, ref=SHA), [check_result("pytest"), summary(**totals)])
    assert tests.result is Result.VERIFIED
    assert set(tests.caveats) == {"PR_CHANGES_CI_CONFIG", "PR_REMOVES_TESTS"}
    lint = verdict(engine, claim(LINT_OK, ref=SHA), [check_result("ruff"), summary(**totals)])
    assert lint.result is Result.VERIFIED
    assert set(lint.caveats) == {"PR_CHANGES_CI_CONFIG", "LINT_SUPPRESSIONS_ADDED"}


def test_removed_tests_are_fine_when_the_code_went_too(engine):
    totals = {"test_cases_removed": 5, "code_removed": True, "code_net": -200}
    result = verdict(
        engine, claim(TESTS_PASS, ref=SHA), [check_result("pytest"), summary(**totals)]
    )
    assert "PR_REMOVES_TESTS" not in result.caveats


def test_an_unreadable_file_list_withdraws_nothing_but_says_so(engine):
    stale = frozenset({"changed_files_summary"})
    result = verdict(engine, claim(TESTS_PASS, ref=SHA), [check_result("pytest")], stale=stale)
    assert result.result is Result.VERIFIED
    assert result.caveats == ("NOT_INSPECTED",)


def test_skipped_matching_jobs_are_a_caution(engine):
    evidence = [check_result("pytest"), check_result("e2e tests", "skipped"), summary()]
    result = verdict(engine, claim(TESTS_PASS, ref=SHA), evidence)
    assert (result.result, result.caveats) == (Result.VERIFIED, ("MATCHING_JOB_SKIPPED",))


def added_tests_evidence(**totals):
    return [changed_file("tests/test_parser.py", "added", 30), summary(**totals)]


def test_added_tests_that_are_all_skipped_are_not_verified(engine):
    evidence = added_tests_evidence(test_source_changed=1, test_cases_added=2, test_skips_net=2)
    result = verdict(engine, claim(TESTS_ADDED), evidence)
    assert (result.result, result.reasons) == (Result.UNVERIFIABLE, ("ADDED_TESTS_ARE_SKIPPED",))


def test_test_files_without_a_counted_test_case_get_no_caution(engine):
    # Retired after the run-2 audit (1 of 6 right): table rows, rstest cases and fixture-driven
    # cases add tests without a new test function.
    result = verdict(engine, claim(TESTS_ADDED), added_tests_evidence(test_source_changed=1))
    assert (result.result, result.caveats) == (Result.VERIFIED, ())


def test_a_summary_missing_fields_withdraws_nothing(engine):
    partial = Evidence("changed_files_summary", "7", T0, {"files": 3})
    result = verdict(engine, claim(TESTS_PASS, ref=SHA), [check_result("pytest"), partial])
    assert result.result is Result.VERIFIED


def test_a_summary_without_the_inspection_field_counts_as_not_inspected(engine):
    partial = Evidence("changed_files_summary", "7", T0, {"files": 3})
    result = verdict(engine, claim(TESTS_PASS, ref=SHA), [check_result("pytest"), partial])
    assert "NOT_INSPECTED" in result.caveats


def test_tolerated_ci_failures_and_build_skips_are_cautions(engine):
    totals = {"ci_failures_tolerated": 1, "build_tests_skipped": 1}
    result = verdict(
        engine, claim(TESTS_PASS, ref=SHA), [check_result("pytest"), summary(**totals)]
    )
    assert (result.result, result.caveats) == (Result.VERIFIED, ("CI_TOLERATES_TEST_FAILURES",))


def test_a_skipped_group_of_new_tests_is_not_verified(engine):
    evidence = added_tests_evidence(
        test_source_changed=1, test_cases_added=2, test_skips_net=1, suite_skipped_cases=2
    )
    result = verdict(engine, claim(TESTS_ADDED), evidence)
    assert (result.result, result.reasons) == (Result.UNVERIFIABLE, ("ADDED_TESTS_ARE_SKIPPED",))
