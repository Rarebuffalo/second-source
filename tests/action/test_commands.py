"""Tests for PR comment commands (action/commands.py)."""

import pytest

from second_source.action.commands import CommandName, may_run, parse_command


@pytest.mark.parametrize(
    ("body", "name", "argument"),
    [
        ("/second-source recheck", CommandName.RECHECK, ""),
        (
            "Thanks!\n/second-source dispute tests were not run on this commit",
            CommandName.DISPUTE,
            "tests were not run on this commit",
        ),
        ("  /second-source RECHECK  ", CommandName.RECHECK, ""),
    ],
)
def test_parses_commands(body, name, argument):
    command = parse_command(body, "alice", "MEMBER")
    assert (command.name, command.argument) == (name, argument)


@pytest.mark.parametrize(
    "body", ["", "please /second-source recheck", "/second-source explode", "/second-sourcerecheck"]
)
def test_ignores_non_commands(body):
    assert parse_command(body, "alice", "MEMBER") is None


@pytest.mark.parametrize(
    ("author", "association", "allowed"),
    [
        ("alice", "MEMBER", True),
        ("bob", "COLLABORATOR", True),
        ("pr-author", "NONE", True),  # the PR author may always ask
        ("PR-Author", "CONTRIBUTOR", True),  # logins are case-insensitive
        ("stranger", "NONE", False),
        ("drive-by", "FIRST_TIME_CONTRIBUTOR", False),
    ],
)
def test_only_the_author_or_writers_may_run_commands(author, association, allowed):
    command = parse_command("/second-source recheck", author, association)
    assert may_run(command, "pr-author") is allowed
