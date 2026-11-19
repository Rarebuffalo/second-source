"""Tests for canonical hashing (core/hashing.py).

Replayability depends on these guarantees: the same logical input always hashes the same,
and any meaningful change produces a different hash.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest

from second_source.core.hashing import canonical_json, sha256_hex
from second_source.core.model import Result


def test_key_order_does_not_change_the_hash():
    assert sha256_hex({"a": 1, "b": 2}) == sha256_hex({"b": 2, "a": 1})


def test_value_changes_change_the_hash():
    assert sha256_hex({"a": 1}) != sha256_hex({"a": 2})


def test_tuples_and_lists_hash_the_same():
    assert canonical_json((1, 2)) == canonical_json([1, 2])


def test_integer_valued_floats_normalise_to_integers():
    # 5 and 5.0 mean the same amount; they must not produce different hashes.
    assert canonical_json({"amount": 5.0}) == canonical_json({"amount": 5})


def test_equal_instants_in_different_timezones_are_distinguished_by_offset():
    # We hash the ISO form, so callers must normalise to UTC before hashing.
    utc = datetime(2026, 1, 1, 12, tzinfo=UTC)
    ist = utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert canonical_json(utc) != canonical_json(ist)


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="naive"):
        canonical_json({"t": datetime(2026, 1, 1)})


def test_enums_and_dataclasses_are_supported():
    @dataclass(frozen=True)
    class Sample:
        result: Result
        when: datetime

    sample = Sample(Result.VERIFIED, datetime(2026, 1, 1, tzinfo=UTC))
    assert '"result":"VERIFIED"' in canonical_json(sample)


def test_durations_are_serialised_as_seconds():
    assert canonical_json(timedelta(minutes=2)) == '{"$dur":120}'
