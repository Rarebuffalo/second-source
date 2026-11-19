"""Non-coding fixture suite: proves the core engine is domain-neutral.

A customer-support agent claims "I issued your refund". The customer_support_demo pack checks
that claim against payment-system records, now (L4) and over time (L5). If this suite ever
needs a change in `second_source.core` to pass, the core has leaked domain assumptions.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from second_source.core.engine import Engine
from second_source.core.model import Claim, Episode, Evidence, Result
from second_source.core.pack import load_pack

PACK_DIR = Path(__file__).parent / "customer_support_demo"
CLAIMED_AT = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
EPISODE = Episode(id="conv-42", kind="conversation", agent="support-bot", opened_at=CLAIMED_AT)
CLAIM = Claim(
    id="claim-1",
    episode_id="conv-42",
    type="REFUND_ISSUED",
    made_at=CLAIMED_AT,
    attributes={"order_id": "ORD-7", "amount": 2000},
    origin="transcript",
    span="Your refund of 2,000 has been processed.",
)


@pytest.fixture(scope="module")
def engine():
    return Engine.from_packs([load_pack(PACK_DIR)])


def refund(ref, amount, status="SUCCESS", *, order_id="ORD-7", version=1):
    return Evidence(
        record_type="refund",
        external_ref=ref,
        observed_at=CLAIMED_AT + timedelta(hours=1),
        version=version,
        attributes={"order_id": order_id, "amount": amount, "status": status},
    )


def verify(engine, evidence, *, hours_later, stale=frozenset()):
    (verdict,) = engine.verify(
        EPISODE, [CLAIM], evidence, now=CLAIMED_AT + timedelta(hours=hours_later), stale=stale
    )
    return verdict


# Each row: description, evidence, hours after the claim, expected verdict, expected reason.
SCENARIOS = [
    (
        "refund found, durability window still open",
        [refund("r1", 2000)],
        24,
        Result.PENDING,
        "IN_FINALITY_WINDOW",
    ),
    (
        "refund found and stayed for the whole window",
        [refund("r1", 2000)],
        24 * 8,
        Result.VERIFIED,
        "REFUND_FOUND",
    ),
    (
        "refund later reversed",
        [refund("r1", 2000, version=1), refund("r1", 2000, "REVERSED", version=2)],
        24 * 3,
        Result.CONTRADICTED,
        "REFUND_REVERSED",
    ),
    ("nothing yet, still inside the grace period", [], 12, Result.PENDING, "AWAITING_REFUND"),
    ("nothing after the grace period", [], 72, Result.CONTRADICTED, "NO_REFUND"),
    (
        "only part of the amount refunded",
        [refund("r1", 500)],
        72,
        Result.CONTRADICTED,
        "PARTIAL_REFUND",
    ),
    (
        "refund for a different order does not count",
        [refund("r9", 2000, order_id="ORD-8")],
        72,
        Result.CONTRADICTED,
        "NO_REFUND",
    ),
]


@pytest.mark.parametrize(
    ("evidence", "hours", "expected", "reason"),
    [row[1:] for row in SCENARIOS],
    ids=[row[0] for row in SCENARIOS],
)
def test_refund_scenarios(engine, evidence, hours, expected, reason):
    verdict = verify(engine, evidence, hours_later=hours)
    assert verdict.result is expected
    assert reason in verdict.reasons


def test_stale_payment_feed_never_produces_a_contradiction(engine):
    verdict = verify(engine, [], hours_later=72, stale=frozenset({"refund"}))
    assert verdict.result is Result.UNVERIFIABLE
    assert verdict.reasons == ("SOURCE_STALE", "SOURCE_STALE")


def test_each_level_is_visible_in_the_check_runs(engine):
    verdict = verify(engine, [refund("r1", 2000)], hours_later=24)
    by_check = {run.check_id: run for run in verdict.check_runs}
    assert by_check["customer_support_demo.refund_issued"].result is Result.VERIFIED
    assert by_check["customer_support_demo.refund_stays_issued"].result is Result.PENDING


def test_results_are_replayable(engine):
    evidence = [refund("r1", 2000)]
    first = verify(engine, evidence, hours_later=24 * 8)
    second = verify(engine, list(evidence), hours_later=24 * 8)
    assert [r.inputs_hash for r in first.check_runs] == [r.inputs_hash for r in second.check_runs]
    assert first.result == second.result
