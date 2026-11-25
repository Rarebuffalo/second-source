"""Randomised oracle tests: CONTRADICTED only where the documented condition holds.

For each contradiction path in docs/packs/coding.md ("CONTRADICTED comes only from these"),
a focused generator builds claims and evidence around that path, and an independent oracle,
written from the documentation table rather than from the check file, says whether a
contradiction is allowed. The property is one-directional: CONTRADICTED implies the oracle's
condition. (The other direction, that a clear failure is caught, is covered by scenario
tests; abstaining is always allowed.) Each path also proves its generator can reach
CONTRADICTED at all, so a test can never pass only because nothing was ever contradicted.

Records are de-duplicated as the engine does it: by record type and external reference,
keeping the highest version.
"""

from datetime import UTC, datetime, timedelta

from hypothesis import find, given, settings
from hypothesis import strategies as st

from second_source.core.engine import Engine
from second_source.core.model import Claim, Episode, Evidence, Result
from second_source.packs.coding import load
from second_source.packs.coding.functions import env_matches, job_matches

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
ENGINE = Engine.from_packs([load()])
EPISODE = Episode(
    id="octo/app#7",
    kind="pull_request",
    agent="codex",
    opened_at=T0,
    attributes={"repo": "octo/app", "number": 7},
)
SHAS = ["abc123", "fff999"]
CLOSE_TOLERANCE = timedelta(minutes=5)
FIND = settings(max_examples=5000, deadline=None, database=None)
CHECK = settings(max_examples=120, deadline=None)

hours = st.integers(min_value=-2, max_value=24 * 40)
times = hours.map(lambda h: T0 + timedelta(hours=h))
minutes = st.integers(min_value=0, max_value=120).map(lambda m: timedelta(minutes=m))


def record(record_type, ref, attributes, version=1):
    kept = {k: v for k, v in attributes.items() if v is not None}
    return Evidence(record_type, ref, T0, kept, version=version)


def latest(evidence, record_type):
    newest = {}
    for e in evidence:
        if e.record_type == record_type:
            if e.external_ref not in newest or e.version > newest[e.external_ref].version:
                newest[e.external_ref] = e
    return [e.attributes for e in newest.values()]


def claim(claim_type, attributes, *, made_at=T0, origin="body"):
    return Claim("c1", EPISODE.id, claim_type, made_at, attributes, origin=origin)


def verdict(case):
    the_claim, evidence, now = case
    (result,) = ENGINE.verify(EPISODE, [the_claim], evidence, now=now)
    return result


def contradicted(case):
    return verdict(case).result is Result.CONTRADICTED


# ---- tests pass / build / lint ---------------------------------------------------------------

JOB_NAMES = ["pytest", "test", "unit-tests", "lint", "ruff", "build", "deploy", "Second Source"]


@st.composite
def ci_cases(draw):
    claim_type = draw(st.sampled_from(["TESTS_PASS", "BUILD_OK", "LINT_OK"]))
    attributes = {"ref": draw(st.sampled_from(SHAS))}
    if draw(st.booleans()):
        attributes["final_ref"] = draw(st.sampled_from(SHAS))
    scope = draw(st.sampled_from([None, "local", "subset"]))
    if scope:
        attributes["scope"] = scope
    evidence = []
    for _ in range(draw(st.integers(0, 5))):
        ref, name = draw(st.sampled_from(SHAS)), draw(st.sampled_from(JOB_NAMES))
        status = draw(st.sampled_from(["completed", "completed", "in_progress", "queued"]))
        conclusion = (
            draw(st.sampled_from(["success", "failure", "cancelled", "timed_out", "skipped"]))
            if status == "completed"
            else None
        )
        evidence.append(
            record(
                "check_result",
                f"{ref}/{name}",
                {
                    "ref": ref,
                    "name": name,
                    "status": status,
                    "conclusion": conclusion,
                    "source": "check_run",
                },
                draw(st.integers(1, 3)),
            )
        )
    for _ in range(draw(st.integers(0, 2))):
        ref = draw(st.sampled_from(SHAS))
        evidence.append(
            record(
                "ci_workflow_run",
                f"{ref}/workflow-run:{draw(st.integers(1, 3))}",
                {
                    "ref": ref,
                    "name": "CI",
                    "status": draw(st.sampled_from(["completed", "in_progress", "waiting"])),
                    "conclusion": draw(st.sampled_from([None, "success", "action_required"])),
                    "created_at": draw(times),
                },
            )
        )
    return (
        claim(claim_type, attributes),
        evidence,
        T0 + draw(minutes) + timedelta(hours=draw(st.integers(0, 12))),
    )


def ci_failure_documented(case):
    """docs: a matching CI job failed on the claim's commit, and on the final head if the PR
    gained commits; never for local or subset claims, timed-out or cancelled jobs."""
    the_claim, evidence, _now = case
    attrs = the_claim.attributes
    if attrs.get("scope") in ("local", "subset"):
        return False
    jobs = latest(evidence, "check_result")

    def failed_on(ref):
        return any(
            j["ref"] == ref
            and job_matches(the_claim.type, j["name"])
            and j.get("conclusion") == "failure"
            for j in jobs
        )

    return failed_on(attrs["ref"]) and failed_on(attrs.get("final_ref", attrs["ref"]))


def test_ci_generator_reaches_contradiction():
    find(ci_cases(), contradicted, settings=FIND)


@CHECK
@given(ci_cases())
def test_ci_contradicts_only_on_a_documented_failure(case):
    if contradicted(case):
        assert ci_failure_documented(case)


# ---- fixes an issue --------------------------------------------------------------------------


@st.composite
def issue_cases(draw):
    origin = draw(st.sampled_from(["body", "commit", "title", "comment"]))
    attributes = {"issue": draw(st.integers(1, 2)), "repo": draw(st.sampled_from(["", "o/r"]))}
    merged = draw(st.booleans())
    merged_at = draw(times)
    evidence = [
        record(
            "pr_state",
            "7",
            {
                "state": "closed" if merged else "open",
                "merged": merged,
                "merged_at": merged_at if merged else None,
                "merge_sha": "abc123" if merged else None,
                "base_ref": draw(st.sampled_from(["main", "main", "dev"])),
                "default_branch": "main",
            },
        )
    ]
    for number in sorted(set(draw(st.lists(st.integers(1, 2), max_size=2)))):
        state = draw(st.sampled_from(["open", "closed"]))
        evidence.append(
            record(
                "issue_state",
                f"octo/app#{number}",
                {
                    "repo": draw(st.sampled_from(["octo/app", "o/r"])),
                    "number": number,
                    "state": state,
                    "closed_at": draw(times) if state == "closed" else None,
                    "last_reopened_at": draw(st.one_of(st.none(), times)),
                },
            )
        )
    now = merged_at + draw(minutes) + timedelta(hours=draw(st.integers(0, 24 * 40)))
    return claim("FIXES_ISSUE", attributes, origin=origin), evidence, now


def issue_open_documented(case):
    """docs: still open after a merge into the default branch (not moments after it), or
    reopened after the merge and still open; either way only for a closing keyword in the body
    or a commit message, same repository."""
    the_claim, evidence, now = case
    if the_claim.origin not in ("body", "commit") or the_claim.attributes["repo"] != "":
        return False
    (pr,) = latest(evidence, "pr_state")
    if not pr["merged"] or pr["base_ref"] != pr["default_branch"]:
        return False
    for i in latest(evidence, "issue_state"):
        if i["number"] != the_claim.attributes["issue"] or i["repo"] != "octo/app":
            continue
        if i["state"] != "open":
            continue
        reopened = i.get("last_reopened_at") is not None and i["last_reopened_at"] > pr["merged_at"]
        if reopened or now >= pr["merged_at"] + CLOSE_TOLERANCE:
            return True
    return False


def test_issue_generator_reaches_contradiction():
    find(issue_cases(), contradicted, settings=FIND)


@CHECK
@given(issue_cases())
def test_issue_contradicts_only_when_documented(case):
    if contradicted(case):
        assert issue_open_documented(case)


# ---- deployed --------------------------------------------------------------------------------


@st.composite
def deploy_cases(draw):
    attributes = {"ref": "abc123", "environment": draw(st.sampled_from(["production", "prod"]))}
    evidence = [
        record(
            "deployment_status",
            f"d{n}",
            {
                "ref": draw(st.sampled_from(SHAS)),
                "environment": draw(st.sampled_from(["production", "staging"])),
                "state": draw(
                    st.sampled_from(
                        [
                            "success",
                            "failure",
                            "error",
                            "in_progress",
                            "queued",
                            "pending",
                            "inactive",
                        ]
                    )
                ),
                "at": draw(times),
                "complete": draw(st.one_of(st.none(), st.booleans())),
            },
        )
        for n in range(draw(st.integers(0, 3)))
    ]
    return claim("DEPLOYED", attributes), evidence, T0 + timedelta(hours=draw(st.integers(0, 6)))


def deploy_failure_documented(case):
    """docs: a deployment of the commit to that environment failed, nothing is still
    deploying, and the source saw every deployment of the commit."""
    the_claim, evidence, _now = case
    mine = [
        d
        for d in latest(evidence, "deployment_status")
        if d["ref"] == the_claim.attributes["ref"]
        and env_matches(d["environment"], the_claim.attributes["environment"])
    ]
    return (
        any(d["state"] in ("failure", "error") for d in mine)
        and not any(d["state"] in ("in_progress", "queued", "pending", "success") for d in mine)
        and all(d.get("complete") is True for d in mine)
    )


def test_deploy_generator_reaches_contradiction():
    find(deploy_cases(), contradicted, settings=FIND)


@CHECK
@given(deploy_cases())
def test_deploy_contradicts_only_when_documented(case):
    if contradicted(case):
        assert deploy_failure_documented(case)


# ---- merge holds -----------------------------------------------------------------------------


@st.composite
def revert_cases(draw):
    evidence = [
        record(
            "revert",
            f"r{n}",
            {
                "reverted_sha": draw(st.sampled_from(SHAS)),
                "at": draw(times),
                "undone": draw(st.one_of(st.none(), st.booleans())),
            },
        )
        for n in range(draw(st.integers(0, 3)))
    ]
    evidence.append(
        record(
            "pr_state",
            "7",
            {
                "state": "closed",
                "merged": True,
                "merged_at": T0,
                "merge_sha": "abc123",
                "base_ref": "main",
                "default_branch": "main",
            },
        )
    )
    return (
        claim("MERGE_HOLDS", {"merge_sha": "abc123"}, origin="merge"),
        evidence,
        T0 + timedelta(hours=draw(st.integers(0, 24 * 40))),
    )


def revert_documented(case):
    """docs: a revert of the merge commit on the base branch, not itself reverted."""
    the_claim, evidence, _now = case
    return any(
        r["reverted_sha"] == the_claim.attributes["merge_sha"] and r.get("undone") is not True
        for r in latest(evidence, "revert")
    )


def test_revert_generator_reaches_contradiction():
    find(revert_cases(), contradicted, settings=FIND)


@CHECK
@given(revert_cases())
def test_merge_holds_contradicts_only_when_documented(case):
    if contradicted(case):
        assert revert_documented(case)
