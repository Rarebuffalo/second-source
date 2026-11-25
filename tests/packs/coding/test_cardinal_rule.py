"""Randomised tests for the cardinal rule: missing or ambiguous evidence gives UNVERIFIABLE,
never CONTRADICTED (docs/ARCHITECTURE.md; docs/packs/coding.md, "Absence is not proof").

Scenario tests (test_checks.py) pin each path; these throw generated claims and evidence at
the real engine and coding pack, including malformed records with missing or odd attributes,
and check properties that must hold for every input:

1. the engine never raises, and every claim gets exactly one verdict;
2. when the evidence a claim's checks read is stale, nothing is CONTRADICTED;
3. with no evidence at all, nothing is CONTRADICTED;
4. "tests added" and "docs updated" are never CONTRADICTED, whatever the evidence;
5. verdicts are deterministic: the same inputs give the same results, reasons and input
   hashes.
"""

from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from second_source.core.engine import Engine
from second_source.core.model import Claim, Episode, Evidence, Result
from second_source.packs.coding import load

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
ENGINE = Engine.from_packs([load()])
EPISODE = Episode(
    id="octo/app#7",
    kind="pull_request",
    agent="codex",
    opened_at=T0,
    attributes={"repo": "octo/app", "number": 7},
)
CLAIM_TYPES = [
    "TESTS_PASS",
    "BUILD_OK",
    "LINT_OK",
    "TESTS_ADDED",
    "DOCS_UPDATED",
    "FIXES_ISSUE",
    "DEPLOYED",
    "MERGE_HOLDS",
]
RECORD_TYPES = [
    "check_result",
    "ci_workflow_run",
    "changed_file",
    "changed_files_summary",
    "issue_state",
    "pr_state",
    "revert",
    "deployment_status",
]
SHAS = ["abc123", "f" * 40]
settings.register_profile(
    "cardinal", max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile("cardinal")

times = st.integers(min_value=-48, max_value=24 * 40).map(lambda h: T0 + timedelta(hours=h))
words = st.sampled_from(
    [
        "completed",
        "in_progress",
        "queued",
        "waiting",
        "success",
        "failure",
        "cancelled",
        "timed_out",
        "skipped",
        "neutral",
        "action_required",
        "open",
        "closed",
        "error",
        "inactive",
        "pending",
        "pytest",
        "test",
        "lint",
        "build",
        "Second Source",
        "README.md",
        "tests/test_x.py",
        "docs/guide.md",
        "src/app.py",
        "added",
        "removed",
        "modified",
        "production",
        "staging",
        "main",
        "dev",
        "octo/app",
        "other/repo",
        "",
    ]
)
values = st.one_of(
    words, st.booleans(), st.integers(min_value=0, max_value=50), times, st.sampled_from(SHAS)
)
ATTRIBUTE_NAMES = [
    "ref",
    "name",
    "status",
    "conclusion",
    "source",
    "created_at",
    "path",
    "additions",
    "deletions",
    "patch_available",
    "test_skips_added",
    "test_cases_added",
    "asserts_removed",
    "test_cmd_weakened",
    "coverage_gate_lowered",
    "uninspected",
    "ci_config_changed",
    "repo",
    "number",
    "state",
    "closed_at",
    "last_reopened_at",
    "merged",
    "merged_at",
    "merge_sha",
    "base_ref",
    "default_branch",
    "reverted_sha",
    "at",
    "undone",
    "environment",
    "complete",
    "facts_version",
    "code_net",
    "removed_test_files",
]


def record(record_type, ref, attributes, version=1):
    return Evidence(
        record_type,
        ref,
        T0,
        {k: v for k, v in attributes.items() if v is not None},
        version=version,
    )


maybe = st.none
shas = st.sampled_from(SHAS)
conclusions = st.one_of(
    maybe(),
    st.sampled_from(
        ["success", "failure", "cancelled", "timed_out", "skipped", "neutral", "action_required"]
    ),
)


@st.composite
def well_formed(draw):
    """A record shaped as the evidence contract says (docs/packs/coding.md), with random
    values, so the checks' contradiction paths are reachable."""
    kind = draw(st.sampled_from(RECORD_TYPES))
    if kind == "check_result":
        name = draw(st.sampled_from(["pytest", "test", "lint", "ruff", "build", "Second Source"]))
        ref = draw(shas)
        return record(
            kind,
            f"{ref}/{name}",
            {
                "ref": ref,
                "name": name,
                "source": "check_run",
                "status": draw(st.sampled_from(["completed", "in_progress", "queued"])),
                "conclusion": draw(conclusions),
            },
            draw(st.integers(1, 3)),
        )
    if kind == "ci_workflow_run":
        ref = draw(shas)
        return record(
            kind,
            f"{ref}/workflow-run:{draw(st.integers(1, 3))}",
            {
                "ref": ref,
                "name": draw(st.sampled_from(["CI", "Second Source"])),
                "status": draw(st.sampled_from(["completed", "in_progress", "queued", "waiting"])),
                "conclusion": draw(conclusions),
                "created_at": draw(st.one_of(maybe(), times)),
            },
        )
    if kind == "issue_state":
        number = draw(st.integers(1, 3))
        return record(
            kind,
            f"octo/app#{number}",
            {
                "repo": draw(st.sampled_from(["octo/app", "other/repo"])),
                "number": number,
                "state": draw(st.sampled_from(["open", "closed"])),
                "closed_at": draw(st.one_of(maybe(), times)),
                "last_reopened_at": draw(st.one_of(maybe(), times)),
            },
        )
    if kind == "pr_state":
        merged = draw(st.booleans())
        return record(
            kind,
            "7",
            {
                "state": "closed" if merged else draw(st.sampled_from(["open", "closed"])),
                "merged": merged,
                "merged_at": draw(times) if merged else None,
                "merge_sha": draw(shas) if merged else None,
                "base_ref": draw(st.sampled_from(["main", "dev"])),
                "default_branch": "main",
            },
        )
    if kind == "revert":
        return record(
            kind,
            draw(st.sampled_from(["r1", "r2"])),
            {
                "reverted_sha": draw(shas),
                "at": draw(times),
                "undone": draw(st.one_of(maybe(), st.booleans())),
            },
        )
    if kind == "deployment_status":
        return record(
            kind,
            draw(st.sampled_from(["d1", "d2"])),
            {
                "ref": draw(shas),
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
                "complete": draw(st.one_of(maybe(), st.booleans())),
            },
        )
    if kind == "changed_file":
        path = draw(st.sampled_from(["tests/test_x.py", "src/app.py", "README.md", "docs/a.md"]))
        return record(
            kind,
            path,
            {
                "path": path,
                "status": draw(st.sampled_from(["added", "modified", "removed"])),
                "additions": draw(st.integers(0, 20)),
                "deletions": draw(st.integers(0, 20)),
                "patch_available": draw(st.booleans()),
                "test_skips_added": draw(st.integers(0, 2)),
                "test_cases_added": draw(st.integers(0, 2)),
            },
        )
    return record(
        kind,
        "7",
        {
            "uninspected": draw(st.integers(0, 2)),
            "ci_config_changed": draw(st.booleans()),
            "test_cmd_weakened": draw(st.integers(0, 1)),
            "removed_test_files": 0,
            "code_net": 0,
        },
    )


@st.composite
def malformed(draw):
    """Any record type with any subset of attributes, well-formed or not."""
    attributes = draw(st.dictionaries(st.sampled_from(ATTRIBUTE_NAMES), values, max_size=12))
    return Evidence(
        record_type=draw(st.sampled_from(RECORD_TYPES)),
        external_ref=draw(st.sampled_from(["a", "b", "c", "octo/app#3", "abc123/pytest"])),
        observed_at=draw(times),
        attributes=attributes,
        version=draw(st.integers(min_value=1, max_value=3)),
    )


evidence_records = lambda: st.one_of(well_formed(), well_formed(), well_formed(), malformed())  # noqa: E731


# What extraction always sets for each claim type (docs/packs/coding.md, claim attributes).
REQUIRED = {
    "TESTS_PASS": {"ref": st.sampled_from(SHAS)},
    "BUILD_OK": {"ref": st.sampled_from(SHAS)},
    "LINT_OK": {"ref": st.sampled_from(SHAS)},
    "FIXES_ISSUE": {"issue": st.integers(1, 3), "repo": st.sampled_from(["", "other/repo"])},
    "DEPLOYED": {
        "ref": st.sampled_from(SHAS),
        "environment": st.sampled_from(["production", "staging"]),
    },
    "MERGE_HOLDS": {"merge_sha": st.sampled_from(SHAS)},
}
OPTIONAL = {
    "final_ref": st.sampled_from(SHAS),
    "final_at": times,
    "scope": st.sampled_from(["local", "subset", "full"]),
    "confidence": st.sampled_from(["high", "low"]),
}


@st.composite
def claims(draw):
    """Claims as extraction makes them; one in five lacks a required attribute (a source or
    extraction bug must still never produce CONTRADICTED)."""
    claim_type = draw(st.sampled_from(CLAIM_TYPES))
    required = REQUIRED.get(claim_type, {})
    attributes = draw(
        st.fixed_dictionaries({} if draw(st.integers(0, 4)) == 0 else required, optional=OPTIONAL)
    )
    return Claim(
        id=f"c-{claim_type}-{draw(st.integers(0, 9))}",
        episode_id=EPISODE.id,
        type=claim_type,
        made_at=draw(times),
        attributes=attributes,
        origin=draw(st.sampled_from(["body", "commit", "title", "comment", "merge"])),
    )


def unique(claim_list):
    return list({c.id: c for c in claim_list}.values())


claim_lists = st.lists(claims(), min_size=1, max_size=4).map(unique)
evidence_lists = st.lists(evidence_records(), max_size=12)


def verify(claim_list, evidence, *, now, stale=frozenset()):
    return ENGINE.verify(EPISODE, claim_list, evidence, now=now, stale=stale)


@given(claim_lists, evidence_lists, times)
def test_never_raises_and_answers_every_claim(claim_list, evidence, now):
    verdicts = verify(claim_list, evidence, now=now)
    assert [v.claim_id for v in verdicts] == [c.id for c in claim_list]


@given(claim_lists, evidence_lists, times)
def test_stale_evidence_never_contradicts(claim_list, evidence, now):
    verdicts = verify(claim_list, evidence, now=now, stale=frozenset(RECORD_TYPES))
    assert all(v.result is not Result.CONTRADICTED for v in verdicts)


@given(claim_lists, times)
def test_no_evidence_never_contradicts(claim_list, now):
    verdicts = verify(claim_list, [], now=now)
    assert all(v.result is not Result.CONTRADICTED for v in verdicts)


@given(claim_lists, evidence_lists, times)
def test_absence_claims_are_never_contradicted(claim_list, evidence, now):
    for claim, v in zip(claim_list, verify(claim_list, evidence, now=now), strict=True):
        if claim.type in ("TESTS_ADDED", "DOCS_UPDATED"):
            assert v.result is not Result.CONTRADICTED


@given(claim_lists, evidence_lists, times)
def test_verdicts_are_deterministic(claim_list, evidence, now):
    def fingerprint(verdicts):
        return [
            (
                v.result,
                v.reasons,
                tuple((r.check_id, r.result, r.inputs_hash) for r in v.check_runs),
            )
            for v in verdicts
        ]

    first = fingerprint(verify(claim_list, evidence, now=now))
    again = fingerprint(verify(claim_list, list(reversed(evidence)), now=now))
    assert first == again  # the order records arrive in does not matter either
