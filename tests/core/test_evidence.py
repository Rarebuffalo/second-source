"""Tests for evidence de-duplication (core/evidence.py)."""

from datetime import UTC, datetime, timedelta

from second_source.core.evidence import dedupe_evidence
from second_source.core.model import Evidence

T0 = datetime(2026, 11, 1, tzinfo=UTC)


def record(ref, *, record_type="payment", observed=T0, version=1, **attributes):
    return Evidence(
        record_type=record_type,
        external_ref=ref,
        observed_at=observed,
        version=version,
        attributes=attributes,
    )


def test_identical_reobservations_are_counted_once():
    # The same payment seen in two uploads must not be double counted.
    first = record("pay-1", amount=500)
    again = record("pay-1", observed=T0 + timedelta(days=1), amount=500)
    assert dedupe_evidence([first, again]) == [again]


def test_higher_version_wins_over_later_observation():
    newer_version = record("pay-1", version=2, status="REVERSED")
    later_but_older_version = record("pay-1", observed=T0 + timedelta(days=5), status="SUCCESS")
    assert dedupe_evidence([newer_version, later_but_older_version]) == [newer_version]


def test_same_ref_in_different_record_types_is_kept_separately():
    payment = record("x-1", record_type="payment")
    refund = record("x-1", record_type="refund")
    assert len(dedupe_evidence([payment, refund])) == 2


def test_output_order_is_stable():
    records = [record("b"), record("a"), record("c", record_type="aaa")]
    refs = [(e.record_type, e.external_ref) for e in dedupe_evidence(records)]
    assert refs == [("aaa", "c"), ("payment", "a"), ("payment", "b")]


def test_empty_input_gives_empty_output():
    assert dedupe_evidence([]) == []


def test_an_exact_tie_is_decided_by_content_not_arrival_order():
    # Found by the randomised cardinal-rule tests: same record, version and observation time,
    # different contents. The arrival order must not change the verdict.
    one, other = record("pay-1", status="SUCCESS"), record("pay-1", status="FAILED")
    assert dedupe_evidence([one, other]) == dedupe_evidence([other, one])
