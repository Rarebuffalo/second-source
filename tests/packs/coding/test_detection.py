"""Tests for coding-agent detection (packs/coding/detection.py)."""

import pytest

from second_source.packs.coding.detection import (
    PullRequestSignals,
    Signal,
    detect_agent,
)


@pytest.mark.parametrize(
    ("signals", "agent", "signal"),
    [
        (PullRequestSignals(author_login="Copilot"), "copilot", Signal.AUTHOR),
        (PullRequestSignals(author_login="devin-ai-integration[bot]"), "devin", Signal.AUTHOR),
        (PullRequestSignals(author_login="google-labs-jules[bot]"), "jules", Signal.AUTHOR),
        (PullRequestSignals(author_login="cursor[bot]"), "cursor", Signal.AUTHOR),
        (
            PullRequestSignals(
                author_login="alice", body="...\n🤖 Generated with [Claude Code](https://x)"
            ),
            "claude_code",
            Signal.BODY_MARKER,
        ),
        (
            PullRequestSignals(
                author_login="alice",
                commit_messages=["Fix parser\n\nCo-Authored-By: Claude <noreply@anthropic.com>"],
            ),
            "claude_code",
            Signal.COMMIT_TRAILER,
        ),
        (
            PullRequestSignals(author_login="bob", head_branch="codex/fix-tests"),
            "codex",
            Signal.BRANCH_PREFIX,
        ),
        (
            PullRequestSignals(author_login="bob", head_branch="cursor/refactor"),
            "cursor",
            Signal.BRANCH_PREFIX,
        ),
    ],
)
def test_detects_agents(signals, agent, signal):
    match = detect_agent(signals)
    assert match is not None
    assert (match.agent, match.signal) == (agent, signal)


def test_human_pull_requests_are_not_detected():
    signals = PullRequestSignals(
        author_login="alice",
        head_branch="feature/login",
        body="Adds login. Tested manually.",
        commit_messages=["Add login\n\nCo-Authored-By: Bob <bob@example.com>"],
    )
    assert detect_agent(signals) is None


def test_author_beats_weaker_signals():
    # The Copilot account opened it, even though the branch looks like Codex.
    signals = PullRequestSignals(author_login="Copilot", head_branch="codex/oops")
    assert detect_agent(signals).agent == "copilot"


def test_logins_must_match_exactly():
    assert detect_agent(PullRequestSignals(author_login="not-copilot")) is None
    assert detect_agent(PullRequestSignals(author_login="copilot-fan")) is None


def test_branch_prefix_must_be_at_the_start():
    assert detect_agent(PullRequestSignals(author_login="x", head_branch="feature/codex/x")) is None


def test_trailer_must_be_its_own_line():
    message = "Mention: Co-Authored-By: Claude is how the trailer looks"
    assert detect_agent(PullRequestSignals(author_login="x", commit_messages=[message])) is None


def test_match_reports_the_detail_that_matched():
    match = detect_agent(PullRequestSignals(author_login="x", head_branch="codex/a"))
    assert match.detail == "codex/"
