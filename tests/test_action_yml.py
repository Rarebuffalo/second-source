"""Consistency checks for action.yml: its inputs must be exactly what the entrypoint reads."""

from pathlib import Path

import yaml

from second_source.action.inputs import read_inputs

ACTION = yaml.safe_load((Path(__file__).parents[1] / "action.yml").read_text(encoding="utf-8"))


def _env_name(input_name):
    return "INPUT_" + input_name.upper().replace("-", "_")


def test_every_input_is_passed_to_the_entrypoint():
    step_env = ACTION["runs"]["steps"][-1]["env"]
    assert {_env_name(name) for name in ACTION["inputs"]} == set(step_env)
    for name in ACTION["inputs"]:
        assert step_env[_env_name(name)] == "${{ inputs." + name + " }}"


def test_input_defaults_are_valid_for_the_entrypoint():
    env = {_env_name(name): spec["default"] for name, spec in ACTION["inputs"].items()}
    env["INPUT_GITHUB_TOKEN"] = "token"  # the real default is an expression
    inputs = read_inputs(env)
    assert (inputs.wait_minutes, inputs.only_agents, inputs.sweep_days) == (10, True, 30)


def test_it_is_a_composite_action_that_runs_the_action_command():
    assert ACTION["runs"]["using"] == "composite"
    assert ACTION["runs"]["steps"][-1]["run"].endswith("second-source action")
    assert all("actions/checkout" not in str(step) for step in ACTION["runs"]["steps"])
