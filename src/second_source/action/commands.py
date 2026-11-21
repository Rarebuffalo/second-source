"""Commands people type in PR comments: ``/second-source recheck`` and ``/second-source dispute``.

Only the PR author and people with write access may run commands; anyone else's comment is
ignored silently, so a public repository cannot be made to spam labels or replies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlencode

from second_source.sources.github.client import GitHubClient
from second_source.sources.github.urls import PullRequestRef

PREFIX = "/second-source"
# Author associations GitHub reports for people with write access to the repository.
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_COMMAND_LINE = re.compile(rf"^\s*{re.escape(PREFIX)}\s+(\w+)\s*(.*?)\s*$", re.MULTILINE)


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
    return Command(name, found.group(2), author, author_association)


def may_run(command: Command, pr_author: str) -> bool:
    """True if the commenter may run commands on this PR."""
    return (
        command.author_association in TRUSTED_ASSOCIATIONS
        or command.author.lower() == pr_author.lower()
    )


# ---- disputes -------------------------------------------------------------------------------

PROJECT_URL = "https://github.com/Rarebuffalo/second-source"
DISPUTED_LABEL = "second-source: disputed"


def wrong_verdict_link(pr_url: str, reason: str) -> str:
    """A link to the project's "wrong verdict" issue form with the PR and reason filled in."""
    query = urlencode({"template": "wrong_verdict.yml", "pr": pr_url, "expected": reason})
    return f"{PROJECT_URL}/issues/new?{query}"


def apply_dispute(client: GitHubClient, ref: PullRequestRef, command: Command) -> None:
    """Record a dispute: label the PR (the label is the only state) and reply with a link
    for reporting the wrong verdict to the project."""
    base = f"/repos/{ref.owner}/{ref.repo}/issues/{ref.number}"
    client.post(f"{base}/labels", {"labels": [DISPUTED_LABEL]})
    reason = command.argument or "(no reason given)"
    reply = (
        f"Thanks @{command.author}, this PR is now marked **disputed** and the Second Source "
        f"check says so.\n\nReason: {reason}\n\nIf a verdict is wrong because Second Source "
        f"misread a claim or the evidence, please [report it here]"
        f"({wrong_verdict_link(ref.url, reason)}) so the rules can be fixed."
    )
    client.post(f"{base}/comments", {"body": reply})
