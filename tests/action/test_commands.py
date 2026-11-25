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


def dispute_client(existing_labels=()):
    writes = []

    def handler(request):
        if request.method == "GET":  # the PR's current labels
            return httpx.Response(200, json=[{"name": name} for name in existing_labels])
        writes.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={})

    return GitHubClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None), writes


def test_dispute_labels_the_pr_and_replies_with_a_report_link():
    client, writes = dispute_client()
    command = parse_command("/second-source dispute CI job is unrelated", "alice", "MEMBER")
    apply_dispute(client, PullRequestRef("octo", "app", 7), command)

    (label_path, label_body), (comment_path, comment_body) = writes
    assert label_path == "/repos/octo/app/issues/7/labels"
    assert label_body == {"labels": [DISPUTED_LABEL]}
    assert comment_path == "/repos/octo/app/issues/7/comments"
    link = re.search(r"\]\((https://[^)]+)\)", comment_body["body"]).group(1)
    query = parse_qs(urlparse(link).query)
    assert query == {"template": ["wrong_verdict.yml"]}  # never prefilled: may be a private PR


def test_the_dispute_reply_never_repeats_the_commenters_text():
    # Security review 1, M3: the reply is posted as the bot; echoing would let anyone who may
    # comment publish mentions and links under its name.
    client, writes = dispute_client()
    text = "/second-source dispute @org/everyone [log in](https://evil.example) <img src=x>"
    apply_dispute(client, PullRequestRef("octo", "app", 7), parse_command(text, "a", "OWNER"))
    reply = writes[-1][1]["body"]
    assert "@" not in reply and "evil.example" not in reply and "<img" not in reply


def test_a_pr_is_replied_to_once_however_many_disputes():
    client, writes = dispute_client(existing_labels=[DISPUTED_LABEL])
    command = parse_command("/second-source dispute again", "alice", "MEMBER")
    apply_dispute(client, PullRequestRef("octo", "app", 7), command)
    assert writes == []
