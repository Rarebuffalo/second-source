"""Action inputs and run context, read from the environment GitHub Actions provides.

action.yml passes each input as an ``INPUT_<NAME>`` environment variable. Values are
validated here once, so the rest of the Action works with typed, trusted settings and a bad
input fails fast with a clear message instead of misbehaving later.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class InputError(ValueError):
    """An input or required environment variable is missing or invalid."""


@dataclass(frozen=True)
class ActionInputs:
    token: str = field(repr=False)  # never shown in logs or reprs
    wait_minutes: int = 10
    only_agents: bool = True
    fail_on_contradiction: bool = False
    sweep_days: int = 30
    ignore_claims: frozenset[str] = frozenset()  # claim types neither checked nor shown


@dataclass(frozen=True)
class RunContext:
    """What GitHub tells every workflow run: the repository and the triggering event."""

    repository: str  # owner/name
    event_name: str
    event: dict[str, Any]
    summary_path: Path | None  # GITHUB_STEP_SUMMARY, absent outside Actions


_TRUE = {"true", "yes", "1", "on"}
_FALSE = {"false", "no", "0", "off", ""}


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(f"INPUT_{name}")
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return default if value == "" else False
    raise InputError(f"input {name.lower().replace('_', '-')} must be true or false, got {raw!r}")


def _number(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = env.get(f"INPUT_{name}", "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise InputError(f"input {name.lower().replace('_', '-')} must be a number") from None
    if not low <= value <= high:
        raise InputError(f"input {name.lower().replace('_', '-')} must be between {low} and {high}")
    return value


# Claim types a repository may switch off (MERGE_HOLDS is implicit in every merge and stays).
IGNORABLE_CLAIMS = frozenset(
    {"TESTS_PASS", "BUILD_OK", "LINT_OK", "TESTS_ADDED", "DOCS_UPDATED", "FIXES_ISSUE", "DEPLOYED"}
)


def _claim_types(env: Mapping[str, str], name: str) -> frozenset[str]:
    raw = env.get(f"INPUT_{name}", "")
    names = frozenset(
        part.strip().upper() for part in raw.replace("\n", ",").split(",") if part.strip()
    )
    unknown = names - IGNORABLE_CLAIMS
    if unknown:
        raise InputError(
            f"input {name.lower().replace('_', '-')} has unknown claim types {sorted(unknown)}; "
            f"use any of {sorted(IGNORABLE_CLAIMS)}"
        )
    return names


def read_inputs(env: Mapping[str, str]) -> ActionInputs:
    token = env.get("INPUT_GITHUB_TOKEN", "").strip()
    if not token:
        raise InputError("input github-token is empty; pass ${{ github.token }}")
    return ActionInputs(
        token=token,
        wait_minutes=_number(env, "WAIT_MINUTES", 10, 0, 60),
        only_agents=_flag(env, "ONLY_AGENTS", True),
        fail_on_contradiction=_flag(env, "FAIL_ON_CONTRADICTION", False),
        sweep_days=_number(env, "SWEEP_DAYS", 30, 1, 90),
        ignore_claims=_claim_types(env, "IGNORE_CLAIMS"),
    )


def read_context(env: Mapping[str, str]) -> RunContext:
    try:
        repository = env["GITHUB_REPOSITORY"]
        event_name = env["GITHUB_EVENT_NAME"]
        event_path = Path(env["GITHUB_EVENT_PATH"])
    except KeyError as missing:
        raise InputError(
            f"{missing.args[0]} is not set; is this running in GitHub Actions?"
        ) from None
    try:
        event = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InputError(f"cannot read the event payload: {error}") from error
    summary = env.get("GITHUB_STEP_SUMMARY")
    return RunContext(repository, event_name, event, Path(summary) if summary else None)
