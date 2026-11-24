"""Tests for `second-source init` (action/setup.py and the CLI command)."""

import io

import pytest
import yaml

from second_source.action.setup import (
    WORKFLOW_FILE,
    WorkflowExists,
    ci_workflow_names,
    default_action_ref,
    workflow_text,
    write_workflow,
)
from second_source.cli.main import main as cli_main


def add_workflow(root, filename, text):
    path = root / ".github" / "workflows" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_the_action_ref_is_the_major_tag_of_this_version():
    assert default_action_ref("0.5.0") == "Rarebuffalo/second-source@v0"
    assert default_action_ref("1.2.3") == "Rarebuffalo/second-source@v1"


def test_finds_ci_workflows_by_name_or_path(tmp_path):
    add_workflow(tmp_path, "ci.yml", "name: CI\non: [push, pull_request]\njobs: {}\n")
    add_workflow(tmp_path, "lint.yaml", "on:\n  pull_request:\njobs: {}\n")  # unnamed
    add_workflow(tmp_path, "release.yml", "name: Release\non:\n  release:\njobs: {}\n")
    add_workflow(tmp_path, "broken.yml", "name: [unclosed\n")
    add_workflow(tmp_path, "second-source.yml", "name: Second Source\non: push\n")
    assert ci_workflow_names(tmp_path) == [".github/workflows/lint.yaml", "CI"]


def test_generated_workflow_is_valid_and_safe():
    document = yaml.safe_load(workflow_text(["CI"]))
    on = document[True]  # YAML 1.1 reads `on` as True
    assert on["workflow_run"]["workflows"] == ["CI"]
    assert set(on) >= {"pull_request_target", "issue_comment", "schedule", "workflow_dispatch"}
    assert document["permissions"]["checks"] == "write"
    steps = document["jobs"]["check"]["steps"]
    assert steps == [{"uses": default_action_ref()}]  # no checkout of PR code
    assert "github.event.pull_request.number" in document["concurrency"]["group"]
    # CI finishing re-checks the same PR: it must share that PR's group.
    assert "workflow_run.pull_requests[0].number" in document["concurrency"]["group"]
    assert document["jobs"]["check"]["timeout-minutes"] > 60  # longer than the longest wait


def test_without_ci_workflows_the_workflow_still_works():
    document = yaml.safe_load(workflow_text([]))
    assert "workflow_run" not in document[True]


def test_write_refuses_to_overwrite_unless_forced(tmp_path):
    path, names = write_workflow(tmp_path)
    assert path == tmp_path / WORKFLOW_FILE and names == []
    with pytest.raises(WorkflowExists):
        write_workflow(tmp_path)
    write_workflow(tmp_path, force=True)


def test_init_command(tmp_path):
    add_workflow(tmp_path, "ci.yml", "name: CI\non: push\n")
    out, err = io.StringIO(), io.StringIO()
    code = cli_main(["init", "--path", str(tmp_path)], env={}, stdout=out, stderr=err)
    assert code == 0 and "re-checks after these CI workflows finish: CI" in out.getvalue()
    code = cli_main(["init", "--path", str(tmp_path)], env={}, stdout=out, stderr=err)
    assert code == 2 and "already exists" in err.getvalue()
