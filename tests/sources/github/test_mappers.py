"""Tests for the pure GitHub JSON mappers (sources/github/mappers.py).

Payloads follow the shapes in GitHub's REST API documentation, trimmed to the fields used.
"""

from datetime import UTC, datetime

from second_source.packs.coding import diff_facts
from second_source.sources.github import mappers

NOW = datetime(2026, 11, 21, tzinfo=UTC)


def pull(**overrides):
    base = {
        "number": 7,
        "title": "Fix parser edge case",
        "body": "All tests pass.",
        "state": "closed",
        "created_at": "2026-11-20T09:00:00Z",
        "closed_at": "2026-11-20T12:00:00Z",
        "merged_at": "2026-11-20T12:00:00Z",
        "merge_commit_sha": "m123",
        "html_url": "https://github.com/Octo/App/pull/7",
        "user": {"login": "agent-bot"},
        "head": {"ref": "codex/fix-parser", "sha": "c2"},
        "base": {"ref": "main", "repo": {"full_name": "Octo/App"}},
    }
    base.update(overrides)
    return base


def commit(sha, date, message="Change"):
    return {"sha": sha, "commit": {"message": message, "committer": {"date": date}}}


COMMITS = [commit("c1", "2026-11-20T08:50:00Z", "Fix parser"), commit("c2", "2026-11-20T09:30:00Z")]


# ---- episode, signals, pr_state --------------------------------------------------------------


def test_episode_uses_lower_case_repo_and_omits_missing_values():
    episode = mappers.episode_from_pull(pull(closed_at=None), agent="codex")
    assert episode.id == "octo/app#7"
    assert episode.attributes["repo"] == "octo/app"
    assert episode.closed_at is None
    assert episode.opened_at == datetime(2026, 11, 20, 9, tzinfo=UTC)


def test_signals_include_commit_messages():
    signals = mappers.signals_from_pull(pull(), COMMITS)
    assert signals.author_login == "agent-bot"
    assert signals.head_branch == "codex/fix-parser"
    assert signals.commit_messages == ("Fix parser", "Change")


def test_pr_state_for_merged_and_open_pulls():
    merged = mappers.pr_state_evidence(pull(), NOW)
    assert merged.attributes == {
        "state": "closed",
        "merged": True,
        "merged_at": datetime(2026, 11, 20, 12, tzinfo=UTC),
        "merge_sha": "m123",
        "base_ref": "main",
    }
    still_open = mappers.pr_state_evidence(
        pull(state="open", merged_at=None, closed_at=None, merge_commit_sha="test-merge"), NOW
    )
    # GitHub fills merge_commit_sha with a test merge for open PRs; it must not leak in.
    assert still_open.attributes == {"state": "open", "merged": False, "base_ref": "main"}


# ---- texts and refs --------------------------------------------------------------------------


def test_body_refers_to_newest_commit_before_the_pr_was_opened():
    texts = {t.origin: t for t in mappers.texts_from_pull(pull(), COMMITS, [])}
    assert texts["body"].ref == "c1"  # c2 was pushed after the PR was opened


def test_editable_texts_also_carry_the_final_head():
    texts = {t.origin: t for t in mappers.texts_from_pull(pull(), COMMITS, [])}
    # Body and title may have been edited after c2 was pushed, so both commits could be meant.
    assert (texts["body"].final_ref, texts["title"].final_ref) == ("c2", "c2")
    assert texts["body"].final_at is not None  # c2's commit time: CI on it starts then
    commit_texts = [t for t in mappers.texts_from_pull(pull(), COMMITS, []) if t.origin == "commit"]
    assert all(t.final_ref is None for t in commit_texts)  # a commit message is exact


def test_no_final_head_when_the_pr_gained_no_commits():
    pr = pull(head={"ref": "codex/fix-parser", "sha": "c1"})
    texts = {t.origin: t for t in mappers.texts_from_pull(pr, [COMMITS[0]], [])}
    assert texts["body"].ref == "c1" and texts["body"].final_ref is None


def test_body_falls_back_to_first_commit_when_all_are_later():
    commits = [commit("c9", "2026-11-20T10:00:00Z")]
    texts = {t.origin: t for t in mappers.texts_from_pull(pull(), commits, [])}
    assert texts["body"].ref == "c9"


def test_commit_messages_refer_to_their_own_commit():
    commits = [t for t in mappers.texts_from_pull(pull(), COMMITS, []) if t.origin == "commit"]
    assert [(t.text, t.ref) for t in commits] == [("Fix parser", "c1"), ("Change", "c2")]


def test_only_the_authors_comments_are_texts():
    comments = [
        {"user": {"login": "agent-bot"}, "created_at": "2026-11-20T10:00:00Z", "body": "Done"},
        {"user": {"login": "reviewer"}, "created_at": "2026-11-20T10:05:00Z", "body": "LGTM"},
    ]
    texts = [t for t in mappers.texts_from_pull(pull(), COMMITS, comments) if t.origin == "comment"]
    assert [(t.text, t.ref) for t in texts] == [("Done", "c2")]


def test_empty_texts_are_dropped():
    texts = mappers.texts_from_pull(pull(body=None), [], [])
    assert [t.origin for t in texts] == ["title"]


# ---- CI ---------------------------------------------------------------------------------------


def test_check_run_is_keyed_by_commit_and_job_with_run_id_version():
    run = {
        "id": 205,
        "name": "pytest",
        "status": "completed",
        "conclusion": "success",
        "completed_at": "2026-11-20T09:10:00Z",
    }
    evidence = mappers.check_run_evidence(run, "c1", NOW)
    assert (evidence.external_ref, evidence.version) == ("c1/pytest", 205)
    assert evidence.attributes["conclusion"] == "success"


def test_running_check_run_omits_conclusion():
    run = {"id": 1, "name": "pytest", "status": "in_progress", "conclusion": None}
    assert "conclusion" not in mappers.check_run_evidence(run, "c1", NOW).attributes


def test_statuses_map_to_check_vocabulary_and_never_collide_with_check_runs():
    pending = mappers.status_evidence({"id": 3, "context": "pytest", "state": "pending"}, "c1", NOW)
    errored = mappers.status_evidence({"id": 4, "context": "ci/lint", "state": "error"}, "c1", NOW)
    assert pending.external_ref == "c1/status:pytest"
    assert pending.attributes["status"] == "in_progress"
    assert "conclusion" not in pending.attributes
    assert errored.attributes["conclusion"] == "failure"


# ---- files, issues, reverts, deployments -----------------------------------------------------


def test_changed_files_keep_counts_and_drop_patches():
    file = {
        "filename": "tests/test_parser.py",
        "status": "added",
        "additions": 20,
        "deletions": 0,
        "patch": "@@ -0,0 +1,2 @@\n+def test_x():\n+    assert zebra_lookup() == []",
    }
    evidence = mappers.changed_file_evidence(file, NOW)
    assert evidence.attributes == {
        "path": "tests/test_parser.py",
        "status": "added",
        "additions": 20,
        "deletions": 0,
        "patch_available": True,
        "test_cases_added": 1,
        "asserts_added": 1,
        "nonblank_added": 2,
    }
    # Only counts are kept: no line of the code itself.
    assert "zebra_lookup" not in repr(evidence.attributes)


def test_a_pr_summary_adds_up_integrity_counts():
    files = [
        {
            "filename": "tests/test_a.py",
            "status": "modified",
            "additions": 3,
            "deletions": 0,
            "patch": "@@ -1,1 +1,2 @@\n+@pytest.mark.skip\n def test_a():",
        },
        {
            "filename": ".github/workflows/ci.yml",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": "@@ -1,1 +1,2 @@\n - run: pytest\n+  continue-on-error: true",
        },
        {"filename": "src/a.py", "status": "modified", "additions": 5, "deletions": 1},
    ]
    records = [mappers.changed_file_evidence(f, NOW) for f in files]
    summary = mappers.files_summary_evidence(records, 7, NOW)
    assert summary.record_type == "changed_files_summary"
    totals = summary.attributes
    # continue-on-error on the test step itself weakens the test command (withdraws).
    assert (totals["test_skips_net"], totals["test_cmd_weakened"], totals["code_net"]) == (
        1,
        1,
        4,
    )
    assert totals["ci_config_changed"] is True
    assert totals["ci_config_files"] == [".github/workflows/ci.yml"]


def test_issue_state_with_last_reopen_time():
    issue = {"number": 12, "state": "closed", "closed_at": "2026-11-20T12:00:05Z"}
    events = [
        {"event": "reopened", "created_at": "2026-11-21T08:00:00Z"},
        {"event": "reopened", "created_at": "2026-11-22T08:00:00Z"},
        {"event": "labeled", "created_at": "2026-11-23T08:00:00Z"},
    ]
    evidence = mappers.issue_evidence(issue, events, "Octo/App", NOW)
    assert evidence.external_ref == "octo/app#12"
    assert evidence.attributes["last_reopened_at"] == datetime(2026, 11, 22, 8, tzinfo=UTC)
    assert evidence.attributes["closed_at"] == datetime(2026, 11, 20, 12, 0, 5, tzinfo=UTC)


def test_pull_requests_listed_as_issues_are_ignored():
    assert mappers.issue_evidence({"number": 9, "pull_request": {}}, [], "o/r", NOW) is None


def test_reverts_by_git_revert_and_by_github_button():
    commits = [
        commit("r1", "2026-11-21T10:00:00Z", 'Revert "Fix"\n\nThis reverts commit abc1234.'),
        commit("r2", "2026-11-22T10:00:00Z", 'Revert "Fix" (#8)\n\nReverts Octo/App#7'),
        commit("x1", "2026-11-22T11:00:00Z", "Unrelated\n\nThis reverts commit ffff000."),
        commit("x2", "2026-11-22T12:00:00Z", "Reverts Octo/App#70"),  # a different PR
    ]
    found = mappers.revert_evidence(
        commits, "abc1234def56", "Octo/App", 7, NOW
    )  # real SHAs are hex
    assert [e.external_ref for e in found] == ["r1", "r2"]
    assert all(e.attributes["reverted_sha"] == "abc1234def56" for e in found)


def test_deployment_uses_latest_status_and_lower_case_environment():
    deployment = {"id": 55, "sha": "c1", "environment": "Staging"}
    statuses = [
        {"id": 2, "state": "success", "created_at": "2026-11-20T10:00:00Z"},
        {"id": 1, "state": "in_progress", "created_at": "2026-11-20T09:55:00Z"},
    ]
    evidence = mappers.deployment_evidence(deployment, statuses, NOW)
    assert evidence.attributes["state"] == "success"
    assert evidence.attributes["environment"] == "staging"
    assert mappers.deployment_evidence(deployment, [], NOW) is None


def test_parse_time():
    assert mappers.parse_time("2026-11-20T09:00:00Z") == datetime(2026, 11, 20, 9, tzinfo=UTC)
    assert mappers.parse_time(None) is None


def test_pr_state_records_the_base_and_default_branch():
    feature = pull(
        base={"ref": "v1.0", "repo": {"full_name": "Octo/App", "default_branch": "main"}}
    )
    attributes = mappers.pr_state_evidence(feature, NOW).attributes
    assert (attributes["base_ref"], attributes["default_branch"]) == ("v1.0", "main")


def test_reverts_by_title_after_a_squash_merge():
    # Revert button + squash merge: the message may keep only the quoted title.
    commits = [
        commit("r1", "2026-11-21T10:00:00Z", 'Revert "Fix parser edge case" (#9)'),
        commit("r2", "2026-11-21T11:00:00Z", 'Revert "Fix parser edge case (#7)" (#10)'),
        commit("r3", "2026-11-21T11:30:00Z", 'revert: "Fix parser edge case" (#12)'),
        commit("x1", "2026-11-21T12:00:00Z", 'Revert "Fix parser edge case in lexer" (#11)'),
        commit("x2", "2026-11-21T13:00:00Z", 'Revert "Fix parser edge case (#70)"'),
    ]
    found = mappers.revert_evidence(
        commits, "abc1234def56", "Octo/App", 7, NOW, title="Fix parser edge case"
    )
    assert [e.external_ref for e in found] == ["r1", "r2", "r3"]


def test_revert_probe_path_avoids_busy_files():
    files = [{"filename": "package-lock.json"}, {"filename": "src/z.ts"}, {"filename": "src/a.ts"}]
    assert mappers.revert_probe_path(files) == "src/a.ts"
    assert mappers.revert_probe_path([{"filename": "yarn.lock"}]) == "yarn.lock"
    assert mappers.revert_probe_path([]) is None


def test_a_title_match_never_overrides_an_explicit_revert_reference():
    # Code review H2: two PRs may share a generic title; the message names the reverted one.
    commits = [
        commit("x1", "2026-11-21T10:00:00Z", 'Revert "Fix bug" (#200)\n\nReverts Octo/App#150'),
        commit("x2", "2026-11-21T11:00:00Z", 'Revert "Fix bug"\n\nThis reverts commit 9999999.'),
        commit("r1", "2026-11-21T12:00:00Z", 'Revert "Fix bug" (#201)\n\nReverts Octo/App#7'),
    ]
    found = mappers.revert_evidence(commits, "abc1234def56", "Octo/App", 7, NOW, title="Fix bug")
    assert [e.external_ref for e in found] == ["r1"]


def test_a_truncated_file_list_counts_as_uninspected():
    record = mappers.changed_file_evidence(
        {
            "filename": "src/a.py",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": "@@ -1 +1 @@\n+x",
        },
        NOW,
    )
    summary = mappers.files_summary_evidence([record], 7, NOW, expected_files=3001)
    assert summary.attributes["uninspected"] == 3000


def test_a_snapshot_without_facts_is_not_read_as_clean():
    old_snapshot = [
        {"path": "tests/test_a.py", "status": "modified", "additions": 3, "deletions": 0}
    ]
    assert diff_facts.summarize(old_snapshot)["uninspected"] == 1
