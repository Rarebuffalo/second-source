"""Tests for Action inputs and run context (action/inputs.py)."""

import json

import pytest

from second_source.action.inputs import InputError, read_context, read_inputs


def test_defaults():
    inputs = read_inputs({"INPUT_GITHUB_TOKEN": "t"})
    assert (inputs.wait_minutes, inputs.only_agents, inputs.fail_on_contradiction) == (
        10,
        True,
        False,
    )
    assert inputs.sweep_days == 30


def test_explicit_values():
    env = {
        "INPUT_GITHUB_TOKEN": "t",
        "INPUT_WAIT_MINUTES": "0",
        "INPUT_ONLY_AGENTS": "false",
        "INPUT_FAIL_ON_CONTRADICTION": "TRUE",
        "INPUT_SWEEP_DAYS": "7",
    }
    inputs = read_inputs(env)
    assert (inputs.wait_minutes, inputs.only_agents, inputs.fail_on_contradiction) == (
        0,
        False,
        True,
    )
    assert inputs.sweep_days == 7


def test_empty_optional_inputs_use_defaults():
    inputs = read_inputs(
        {"INPUT_GITHUB_TOKEN": "t", "INPUT_WAIT_MINUTES": "", "INPUT_ONLY_AGENTS": ""}
    )
    assert inputs.wait_minutes == 10 and inputs.only_agents


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "github-token is empty"),
        ({"INPUT_GITHUB_TOKEN": "t", "INPUT_WAIT_MINUTES": "ten"}, "wait-minutes must be a number"),
        ({"INPUT_GITHUB_TOKEN": "t", "INPUT_WAIT_MINUTES": "99"}, "between 0 and 60"),
        (
            {"INPUT_GITHUB_TOKEN": "t", "INPUT_ONLY_AGENTS": "maybe"},
            "only-agents must be true or false",
        ),
    ],
)
def test_invalid_inputs_fail_clearly(env, message):
    with pytest.raises(InputError, match=message):
        read_inputs(env)


def test_token_is_not_in_the_repr():
    assert "secret-token" not in repr(read_inputs({"INPUT_GITHUB_TOKEN": "secret-token"}))


def test_context_reads_the_event_payload(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"action": "opened"}))
    context = read_context(
        {
            "GITHUB_REPOSITORY": "octo/app",
            "GITHUB_EVENT_NAME": "pull_request_target",
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        }
    )
    assert context.repository == "octo/app" and context.event == {"action": "opened"}
    assert context.summary_path == tmp_path / "summary.md"


def test_context_outside_actions_is_an_error():
    with pytest.raises(InputError, match="GITHUB_REPOSITORY is not set"):
        read_context({})


def test_unreadable_event_payload_is_an_error(tmp_path):
    env = {
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_EVENT_PATH": str(tmp_path / "missing.json"),
    }
    with pytest.raises(InputError, match="cannot read the event payload"):
        read_context(env)


def test_claim_types_can_be_switched_off():
    inputs = read_inputs(
        {"INPUT_GITHUB_TOKEN": "t", "INPUT_IGNORE_CLAIMS": " lint_ok, DOCS_UPDATED\n"}
    )
    assert inputs.ignore_claims == {"LINT_OK", "DOCS_UPDATED"}
    with pytest.raises(InputError, match="unknown claim types"):
        read_inputs({"INPUT_GITHUB_TOKEN": "t", "INPUT_IGNORE_CLAIMS": "LINT, MERGE_HOLDS"})
