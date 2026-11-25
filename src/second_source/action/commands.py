"""Commands people type in PR comments: ``/second-source recheck`` and ``/second-source dispute``.

Only the PR author and people with write access may run commands; anyone else's comment is
ignored silently, so a public repository cannot be made to spam labels or replies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from second_source.sources.github.client import GitHubClient
from second_source.sources.github.urls import PullRequestRef

PREFIX = "/second-source"
# Author associations GitHub reports for people with write access to the repository.
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
# One line only ([ \t], not \s): the argument must never come from the next line, and the
# pattern stays linear on long comments (security review 1, M3 and H2).
_COMMAND_LINE = re.compile(rf"^[ \t]*{re.escape(PREFIX)}[ \t]+(\w+)(.*)$", re.MULTILINE)


class CommandName(StrEnum):
    RECHECK = "recheck"
    DISPUTE = "dispute"


@dataclass(frozen=True)
class Command:
    name: CommandName
    argument: str  # the dispute reason; empty for recheck
    author: str
    author_association: str


def parse_command(body: str, author: str, author_association: str) -> Command | None:
    """The first ``/second-source <command>`` line in a comment, or None if there is none or
    the command is unknown."""
    found = _COMMAND_LINE.search(body or "")
    if found is None:
        return None
    try:
        name = CommandName(found.group(1).lower())
    except ValueError:
        return None
    return Command(name, found.group(2).strip(), author, author_association)


def may_run(command: Command, pr_author: str) -> bool:
    """True if the commenter may run commands on this PR."""
    return (
        command.author_association in TRUSTED_ASSOCIATIONS
        or command.author.lower() == pr_author.lower()
    )


# ---- disputes -------------------------------------------------------------------------------

PROJECT_URL = "https://github.com/Rarebuffalo/second-source"
DISPUTED_LABEL = "second-source: disputed"


# The issue form, not prefilled: a prefilled link would copy a private repository's PR URL
# and the commenter's text into a public issue (security review 1, L5).
WRONG_VERDICT_FORM = f"{PROJECT_URL}/issues/new?template=wrong_verdict.yml"


def apply_dispute(client: GitHubClient, ref: PullRequestRef, command: Command) -> None:
    """Record a dispute: label the PR (the label is the only state) and, the first time only,
    reply with where to report a wrong verdict.

    The reply never repeats the commenter's text: it is posted as the bot, so echoing it would
    let anyone who may comment publish mentions, links or images under the bot's name
    (security review 1, M3). The reason stays in the comment that gave it.
    """
    base = f"/repos/{ref.owner}/{ref.repo}/issues/{ref.number}"
    labels = client.get_paginated(f"{base}/labels")
    if any(label.get("name") == DISPUTED_LABEL for label in labels):
        return  # already disputed: one reply per PR, however many commands follow
    client.post(f"{base}/labels", {"labels": [DISPUTED_LABEL]})
    reply = (
        "This PR is now marked **disputed**, and the Second Source check says so. The reason "
        "is in the comment above.\n\nIf a verdict is wrong because Second Source misread a "
        f"claim or the evidence, please [report it]({WRONG_VERDICT_FORM}) so the rules can be "
        "fixed (for a private repository, describe it without the link)."
    )
    client.post(f"{base}/comments", {"body": reply})
