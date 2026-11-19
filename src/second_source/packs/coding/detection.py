"""Detect which coding agent (if any) authored a pull request.

Detection is a list of data rules, checked in order of how reliable the signal is:

1. the PR author account (bots and apps have fixed logins),
2. markers the agent writes into the PR body,
3. ``Co-Authored-By`` trailers in commit messages,
4. the head branch prefix (weakest: a human can create a ``codex/...`` branch).

The first matching rule wins. Rules are versioned with the pack; the benchmark measures their
precision against the AIDev dataset's agent labels.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class Signal(StrEnum):
    AUTHOR = "author"
    BODY_MARKER = "body_marker"
    COMMIT_TRAILER = "commit_trailer"
    BRANCH_PREFIX = "branch_prefix"


@dataclass(frozen=True)
class PullRequestSignals:
    """The facts about a PR that detection looks at. Sources fill this in."""

    author_login: str
    head_branch: str = ""
    body: str = ""
    commit_messages: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class AgentMatch:
    agent: str  # stable agent id, e.g. "claude_code"
    signal: Signal  # which kind of evidence identified it
    detail: str  # the exact value that matched (for transparency in reports)


@dataclass(frozen=True)
class _Rule:
    agent: str
    signal: Signal
    pattern: re.Pattern[str]


def _rule(agent: str, signal: Signal, pattern: str) -> _Rule:
    return _Rule(agent, signal, re.compile(pattern, re.IGNORECASE))


# Ordered from most to least reliable. Logins are matched exactly (anchored).
RULES: tuple[_Rule, ...] = (
    _rule("copilot", Signal.AUTHOR, r"^copilot$"),
    _rule("devin", Signal.AUTHOR, r"^devin-ai-integration\[bot\]$"),
    _rule("jules", Signal.AUTHOR, r"^google-labs-jules\[bot\]$"),
    _rule("cursor", Signal.AUTHOR, r"^(cursor\[bot\]|cursoragent)$"),
    _rule("claude_code", Signal.BODY_MARKER, r"generated with \[?claude code\]?"),
    _rule("claude_code", Signal.COMMIT_TRAILER, r"^co-authored-by:\s*claude\b"),
    _rule("codex", Signal.BRANCH_PREFIX, r"^codex/"),
    _rule("copilot", Signal.BRANCH_PREFIX, r"^copilot/"),
    _rule("cursor", Signal.BRANCH_PREFIX, r"^cursor/"),
)

AGENTS = frozenset(rule.agent for rule in RULES)


def detect_agent(signals: PullRequestSignals) -> AgentMatch | None:
    """Return the agent that authored the PR, or None if no rule matches (likely a human)."""
    for rule in RULES:
        for candidate in _candidates(rule.signal, signals):
            found = rule.pattern.search(candidate)
            if found:
                return AgentMatch(rule.agent, rule.signal, found.group(0).strip())
    return None


def _candidates(signal: Signal, signals: PullRequestSignals) -> list[str]:
    """The text values a rule of this signal kind should be tested against."""
    if signal is Signal.AUTHOR:
        return [signals.author_login]
    if signal is Signal.BODY_MARKER:
        return [signals.body]
    if signal is Signal.COMMIT_TRAILER:
        # Trailers are lines; test each line of each commit message on its own.
        return [
            line.strip() for message in signals.commit_messages for line in message.splitlines()
        ]
    return [signals.head_branch]
