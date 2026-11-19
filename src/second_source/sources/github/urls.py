"""Parse pull-request references that users paste: URLs and ``owner/repo#123`` shorthands."""

from __future__ import annotations

import re
from dataclasses import dataclass

# GitHub owner and repository names: letters, digits, '-', '_' and '.'.
_NAME = r"[A-Za-z0-9_.-]+"
_URL = re.compile(
    rf"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>{_NAME})/(?P<repo>{_NAME})"
    r"/pulls?/(?P<number>\d+)(?:[/?#].*)?$"
)
_SHORT = re.compile(rf"^(?P<owner>{_NAME})/(?P<repo>{_NAME})(?:#|/pull/)(?P<number>\d+)$")


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    number: int

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    def __str__(self) -> str:
        return f"{self.full_name}#{self.number}"


def parse_pull_request(text: str) -> PullRequestRef:
    """Parse a PR reference, or raise ValueError with an example of what is accepted.

    Accepts ``https://github.com/o/r/pull/7`` (with or without extra path such as
    ``/files``), ``github.com/o/r/pull/7``, ``o/r#7`` and ``o/r/pull/7``.
    """
    candidate = text.strip()
    found = _URL.match(candidate) or _SHORT.match(candidate)
    if not found or found.group("repo") in {".", ".."}:
        raise ValueError(
            f"not a pull request reference: {text!r} "
            "(expected e.g. https://github.com/owner/repo/pull/123 or owner/repo#123)"
        )
    number = int(found.group("number"))
    if number <= 0:
        raise ValueError(f"pull request numbers start at 1: {text!r}")
    return PullRequestRef(found.group("owner"), found.group("repo"), number)
