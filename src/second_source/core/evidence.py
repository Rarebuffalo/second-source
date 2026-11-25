"""Evidence helpers that every pack relies on."""

from __future__ import annotations

from collections.abc import Iterable

from second_source.core.hashing import canonical_json
from second_source.core.model import Evidence


def _rank(record: Evidence) -> tuple[int, object, str]:
    # The contents break exact ties, so the result never depends on the order records arrive in.
    return (record.version, record.observed_at, canonical_json(dict(record.attributes)))


def dedupe_evidence(records: Iterable[Evidence]) -> list[Evidence]:
    """Keep one record per (record_type, external_ref): the latest version, then latest observation,
    then (for an exact tie) the record whose contents sort last, so the order is irrelevant.

    Re-observing the same record (for example a re-uploaded file or a repeated API read) must never
    count it twice.
    """
    latest: dict[tuple[str, str], Evidence] = {}
    for record in records:
        key = (record.record_type, record.external_ref)
        current = latest.get(key)
        if current is None or _rank(record) > _rank(current):
            latest[key] = record
    return sorted(latest.values(), key=lambda e: (e.record_type, e.external_ref))
