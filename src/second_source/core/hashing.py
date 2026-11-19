"""Canonical hashing so identical inputs always produce identical hashes (replayability)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


def _normalise(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _normalise(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed; use timezone-aware UTC times")
        return {"$ts": value.isoformat()}
    if isinstance(value, timedelta):
        # Normalise the number too, so 120 s and 120.0 s hash identically.
        return {"$dur": _normalise(value.total_seconds())}
    if isinstance(value, Mapping):
        return {str(k): _normalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def canonical_json(value: Any) -> str:
    """Serialise with sorted keys and no insignificant whitespace."""
    return json.dumps(_normalise(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
