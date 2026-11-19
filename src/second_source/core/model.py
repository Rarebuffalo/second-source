"""Domain-neutral data model.

Every agent type (coding, customer-facing, tool-using, ...) is described with the same
objects: an Episode (one unit of agent work), Claims the agent made, Evidence observed in
the systems the agent acted on, and Verdicts produced by Checks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Result(StrEnum):
    """Outcome of a check or a verdict."""

    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"
    PENDING = "PENDING"
    UNVERIFIABLE = "UNVERIFIABLE"


class CheckKind(StrEnum):
    RULE = "rule"
    STATE = "state"
    TEMPORAL = "temporal"
    JUDGE = "judge"
    HUMAN = "human"


# Kinds whose results may decide a verdict on their own. Judge checks are shadow-only in v1.
DETERMINISTIC_KINDS = frozenset({CheckKind.RULE, CheckKind.STATE, CheckKind.TEMPORAL, CheckKind.HUMAN})


class Level(StrEnum):
    L1 = "L1"  # output quality
    L2 = "L2"  # instruction / policy adherence
    L3 = "L3"  # action correctness
    L4 = "L4"  # effect happened in the system of record
    L5 = "L5"  # effect stayed true over time


@dataclass(frozen=True)
class Episode:
    """One unit of agent work (a pull request, a conversation, a task run)."""

    id: str
    kind: str
    agent: str
    opened_at: datetime
    closed_at: datetime | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Claim:
    """Something the agent asserted about its work."""

    id: str
    episode_id: str
    type: str
    made_at: datetime
    attributes: Mapping[str, Any] = field(default_factory=dict)
    origin: str = "unknown"  # where the claim came from (e.g. title, body, transcript)
    span: str | None = None  # the exact text supporting the claim


@dataclass(frozen=True)
class Evidence:
    """An observation from a system of record, at a point in time."""

    record_type: str
    external_ref: str
    observed_at: datetime
    attributes: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None
    version: int = 1  # higher version = later observation of the same record


@dataclass(frozen=True)
class CheckRun:
    """One evaluation of one check against one claim, with frozen, hashed inputs."""

    check_id: str
    check_version: str
    kind: CheckKind
    level: Level
    claim_id: str
    result: Result
    reason: str
    inputs_hash: str
    derived: Mapping[str, Any] = field(default_factory=dict)
    explanation: str | None = None


@dataclass(frozen=True)
class Verdict:
    """The aggregate result for a claim."""

    claim_id: str
    result: Result
    reasons: tuple[str, ...]
    check_runs: tuple[CheckRun, ...]
