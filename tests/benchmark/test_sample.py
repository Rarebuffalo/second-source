"""Tests for agent-stratified sampling (benchmark/sample.py) against the fake grouped table."""

import httpx
import pytest

from second_source.benchmark.aidev import AidevClient, AidevError
from second_source.benchmark.sample import (
    Block,
    draw_sample,
    locate_blocks,
    read_sample,
    sample_block,
    write_sample,
)


def client_for(fake):
    return AidevClient(transport=httpx.MockTransport(fake), sleep=lambda _s: None)


def test_locate_blocks_finds_every_agent_block(viewer):
    blocks = locate_blocks(client_for(viewer()))
    assert [block.agent for block in blocks] == [
        "codex",
        "claude_code",
        "cursor",
        "devin",
        "jules",
        "copilot",
    ]
    assert [block.size for block in blocks] == [size for _label, size in viewer.BLOCKS]
    assert blocks[0].start == 0 and blocks[-1].end == 1000


def test_locate_blocks_is_cheap(viewer):
    fake = viewer()
    locate_blocks(client_for(fake))
    assert len(fake.requests) < 6 * 12  # a binary search per block, not a scan


def test_locate_blocks_rejects_tables_not_grouped_by_agent(viewer):
    with pytest.raises(AidevError, match="not grouped by agent"):
        locate_blocks(client_for(viewer(total=60, label_for=viewer.interleaved)))


def test_sample_block_respects_filter_and_stays_inside_the_block(viewer):
    block = Block("cursor", 600, 700)
    rows = sample_block(
        client_for(viewer()), block, count=10, seed=1, accept=lambda pull: pull.merged
    )
    assert len(rows) == 10
    numbers = [int(row.url.rsplit("/", 1)[1]) for row in rows]
    assert all(600 <= n < 700 for n in numbers)
    assert all(row.merged for row in rows)
    assert len(set(numbers)) == 10  # drawn without replacement


def test_sample_is_deterministic_per_seed(viewer):
    first = draw_sample(client_for(viewer()), per_agent=5, seed=7)
    again = draw_sample(client_for(viewer()), per_agent=5, seed=7)
    other = draw_sample(client_for(viewer()), per_agent=5, seed=8)
    assert first.rows == again.rows
    assert first.rows != other.rows


def test_one_agents_quota_does_not_change_another_agents_rows(viewer):
    small = draw_sample(client_for(viewer()), per_agent=3, seed=7, agents=["devin"])
    large = draw_sample(client_for(viewer()), per_agent=3, seed=7)
    assert small.rows == tuple(row for row in large.rows if row.agent == "devin")


def test_sample_is_stratified_and_filtered(viewer):
    sample = draw_sample(client_for(viewer()), per_agent=4, seed=3, filter_name="merged_with_body")
    counts = {agent: 0 for agent in ["codex", "claude_code", "cursor", "devin", "jules", "copilot"]}
    for row in sample.rows:
        counts[row.agent] += 1
        assert row.merged and row.body
    assert set(counts.values()) == {4}
    assert sample.header["revision"] == "abc123"
    assert sample.header["seed"] == 3 and sample.shortfall == {}


def test_shortfall_is_reported_when_a_block_is_too_small(viewer):
    sample = draw_sample(client_for(viewer()), per_agent=60, seed=1, agents=["jules"])
    assert 0 < sample.shortfall["jules"] <= 60
    assert sample.header["shortfall"] == sample.shortfall


def test_unknown_agents_are_rejected(viewer):
    with pytest.raises(ValueError, match="not in the dataset"):
        draw_sample(client_for(viewer()), per_agent=1, seed=1, agents=["nobody"])


def test_sample_files_round_trip(viewer, tmp_path):
    sample = draw_sample(client_for(viewer()), per_agent=2, seed=5)
    path = tmp_path / "nested" / "sample.jsonl"
    write_sample(sample, path)
    loaded = read_sample(path)
    assert loaded.rows == sample.rows
    assert loaded.header == sample.header


def test_header_must_come_first(viewer, tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(
        '{"url": "u", "agent": "a", "title": "", "body": "", "state": "", '
        '"created_at": null, "merged_at": null}\n{"header": {}}\n'
    )
    with pytest.raises(ValueError, match="header must be the first record"):
        read_sample(path)


def test_excluded_urls_are_skipped_and_replaced(viewer):
    first = draw_sample(client_for(viewer()), per_agent=4, seed=11, agents=["devin"])
    grown = draw_sample(
        client_for(viewer()),
        per_agent=4,
        seed=11,
        agents=["devin"],
        exclude=[row.url for row in first.rows],
    )
    assert len(grown.rows) == 4
    assert not {row.url for row in first.rows} & {row.url for row in grown.rows}
    assert grown.header["excluded"] == 4
