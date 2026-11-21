"""Tests for PR comment commands (action/commands.py)."""

import json
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from second_source.action.commands import (
    DISPUTED_LABEL,
    CommandName,
    apply_dispute,
    may_run,
    parse_command,
)
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.urls import PullRequestRef


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


def test_dispute_labels_the_pr_and_replies_with_a_report_link():
    writes = []

    def handler(request):
        writes.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={})

    client = GitHubClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None)
    command = parse_command("/second-source dispute CI job is unrelated", "alice", "MEMBER")
    apply_dispute(client, PullRequestRef("octo", "app", 7), command)

    (label_path, label_body), (comment_path, comment_body) = writes
    assert label_path == "/repos/octo/app/issues/7/labels"
    assert label_body == {"labels": [DISPUTED_LABEL]}
    assert comment_path == "/repos/octo/app/issues/7/comments"
    assert "@alice" in comment_body["body"] and "CI job is unrelated" in comment_body["body"]
    link = re.search(r"\]\((https://[^)]+)\)", comment_body["body"]).group(1)
    query = parse_qs(urlparse(link).query)
    assert query["template"] == ["wrong_verdict.yml"]
    assert query["pr"] == ["https://github.com/octo/app/pull/7"]
