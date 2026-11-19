"""Tests for the core data model (core/model.py)."""

import dataclasses
from datetime import UTC, datetime

import pytest

from second_source.core.model import (
    DETERMINISTIC_KINDS,
    CheckKind,
    Claim,
    Episode,
    Result,
)


def test_models_are_immutable():
    episode = Episode(id="e1", kind="task", agent="agent-x", opened_at=datetime.now(UTC))
    with pytest.raises(dataclasses.FrozenInstanceError):
        episode.agent = "someone-else"


def test_result_values_are_stable_strings():
    # These strings appear in outputs and stored results; renaming them is a breaking change.
    assert [r.value for r in Result] == ["VERIFIED", "CONTRADICTED", "PENDING", "UNVERIFIABLE"]


def test_judge_checks_are_not_deterministic():
    assert CheckKind.JUDGE not in DETERMINISTIC_KINDS
    assert {CheckKind.RULE, CheckKind.STATE, CheckKind.TEMPORAL} <= DETERMINISTIC_KINDS


def test_claim_defaults():
    claim = Claim(id="c1", episode_id="e1", type="ANY", made_at=datetime.now(UTC))
    assert claim.origin == "unknown"
    assert claim.span is None
    assert dict(claim.attributes) == {}
