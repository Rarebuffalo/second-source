"""Consistency checks for action.yml: its inputs must be exactly what the entrypoint reads."""

from pathlib import Path

import yaml

from second_source.action.inputs import read_inputs

ACTION = yaml.safe_load((Path(__file__).parents[1] / "action.yml").read_text(encoding="utf-8"))


def _env_name(input_name):
    return "INPUT_" + input_name.upper().replace("-", "_")


def test_every_input_is_passed_to_the_entrypoint():
    step_env = ACTION["runs"]["steps"][-1]["env"]
    inputs_env = {key for key in step_env if key.startswith("INPUT_")}
    assert {_env_name(name) for name in ACTION["inputs"]} == inputs_env
    for name in ACTION["inputs"]:
        assert step_env[_env_name(name)] == "${{ inputs." + name + " }}"


def test_input_defaults_are_valid_for_the_entrypoint():
    env = {_env_name(name): spec["default"] for name, spec in ACTION["inputs"].items()}
    env["INPUT_GITHUB_TOKEN"] = "token"  # the real default is an expression
    inputs = read_inputs(env)
    assert (inputs.wait_minutes, inputs.only_agents, inputs.sweep_days) == (10, True, 30)


def test_it_is_a_composite_action_that_runs_the_action_command():
    assert ACTION["runs"]["using"] == "composite"
    assert ACTION["runs"]["steps"][-1]["run"].endswith("python -m second_source.cli.main action")
    assert all("actions/checkout" not in str(step) for step in ACTION["runs"]["steps"])


def test_the_project_is_never_built_and_the_token_comes_after_the_install():
    # Security review 1, H1: building would fetch an unpinned build backend from PyPI.
    steps = ACTION["runs"]["steps"]
    install = next(step for step in steps if "uv sync" in step.get("run", ""))
    assert "--frozen" in install["run"] and "--no-install-project" in install["run"]
    assert "INPUT_GITHUB_TOKEN" not in install.get("env", {})
    assert "--no-sync" in steps[-1]["run"]
    assert steps[-1]["env"]["PYTHONPATH"] == "${{ github.action_path }}/src"
    assert "--no-build" in install["run"]
    assert steps[-1]["env"]["PYTHONSAFEPATH"] == "1"  # the workspace is never importable
