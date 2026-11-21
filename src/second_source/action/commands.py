"""Commands people type in PR comments: ``/second-source recheck`` and ``/second-source dispute``.

Only the PR author and people with write access may run commands; anyone else's comment is
ignored silently, so a public repository cannot be made to spam labels or replies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

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
