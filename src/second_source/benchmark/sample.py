"""Deterministic, agent-stratified samples of AIDev pull requests (docs/benchmark.md, section 2).

The curated AIDev table stores each agent's rows as one contiguous block. So we:

1. **Locate blocks:** find where each agent's rows start and end with a binary search on single
   rows, then check the blocks really are contiguous (an agent seen in two blocks is an error,
   because the binary search would then be meaningless).
2. **Draw rows:** for each agent, pick random row offsets inside its block (without
   replacement) and keep rows that pass the filters, until the agent's quota is full or the
   draw budget is spent.

Each agent has its own random stream, seeded from ``(seed, agent)``. The same seed and dataset
revision therefore always give the same sample, and changing one agent's quota does not change
the rows drawn for any other agent.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from second_source import __version__
from second_source.benchmark.aidev import CONFIG, DATASET, AidevClient, AidevError, AidevPullRequest

Filter = Callable[[AidevPullRequest], bool]

# How many rows we may read per wanted row before giving up on an agent's quota.
DRAWS_PER_ROW = 5


@dataclass(frozen=True)
class Block:
    """Rows ``start .. end-1`` of the table all belong to ``agent``."""

    agent: str
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Sample:
    """A drawn sample plus everything needed to reproduce it."""

    header: dict[str, Any]
    rows: tuple[AidevPullRequest, ...]
    shortfall: dict[str, int] = field(default_factory=dict)  # agent -> rows still missing


def has_body(pull: AidevPullRequest) -> bool:
    """Golden-set filter: a PR without a body cannot contain claims."""
    return bool(pull.body.strip())


def merged_with_body(pull: AidevPullRequest) -> bool:
    """Benchmark filter: merged PRs only (durability and most evidence need a merge)."""
    return pull.merged and has_body(pull)


FILTERS: dict[str, Filter] = {"has_body": has_body, "merged_with_body": merged_with_body}


def locate_blocks(client: AidevClient) -> tuple[Block, ...]:
    """Find each agent's contiguous block of rows. Costs about 20 requests per agent."""
    total = client.page(0, 1).total
    cache: dict[int, str] = {}

    def agent_at(offset: int) -> str:
        if offset not in cache:
            cache[offset] = client.page(offset, 1).rows[0].agent
        return cache[offset]

    blocks: list[Block] = []
    start = 0
    while start < total:
        agent = agent_at(start)
        # Binary search for the first row after ``start`` with a different agent.
        # Invariant: agent_at(low) == agent; high is past the end of the block.
        low, high = start, total
        while high - low > 1:
            middle = (low + high) // 2
            if agent_at(middle) == agent:
                low = middle
            else:
                high = middle
        blocks.append(Block(agent, start, high))
        start = high

    agents = [block.agent for block in blocks]
    repeated = sorted({agent for agent in agents if agents.count(agent) > 1})
    if repeated:
        raise AidevError(f"table is not grouped by agent (split blocks for {repeated})")
    return tuple(blocks)


def sample_block(
    client: AidevClient, block: Block, *, count: int, seed: int, accept: Filter
) -> list[AidevPullRequest]:
    """Up to ``count`` random rows from one block that pass ``accept``."""
    rng = random.Random(f"{seed}:{block.agent}")
    budget = min(block.size, count * DRAWS_PER_ROW)
    chosen: list[AidevPullRequest] = []
    for offset in rng.sample(range(block.start, block.end), budget):
        if len(chosen) == count:
            break
        pull = client.page(offset, 1).rows[0]
        if accept(pull):
            chosen.append(pull)
    return chosen


def draw_sample(
    client: AidevClient,
    *,
    per_agent: int,
    seed: int,
    filter_name: str = "has_body",
    agents: Iterable[str] | None = None,
) -> Sample:
    """Draw ``per_agent`` PRs for each agent (all agents in the table unless given)."""
    accept = FILTERS[filter_name]
    blocks = locate_blocks(client)
    wanted = set(agents) if agents is not None else {block.agent for block in blocks}
    missing = wanted - {block.agent for block in blocks}
    if missing:
        raise ValueError(f"agents not in the dataset: {sorted(missing)}")

    rows: list[AidevPullRequest] = []
    shortfall: dict[str, int] = {}
    for block in blocks:
        if block.agent not in wanted:
            continue
        chosen = sample_block(client, block, count=per_agent, seed=seed, accept=accept)
        rows.extend(chosen)
        if len(chosen) < per_agent:
            shortfall[block.agent] = per_agent - len(chosen)

    header = {
        "dataset": DATASET,
        "config": CONFIG,
        "revision": client.revision(),
        "seed": seed,
        "per_agent": per_agent,
        "filter": filter_name,
        "blocks": [[block.agent, block.start, block.end] for block in blocks],
        "shortfall": shortfall,
        "tool_version": __version__,
    }
    return Sample(header=header, rows=tuple(rows), shortfall=shortfall)


# ---- JSON Lines files: one header record, then one record per pull request -----------------


def write_sample(sample: Sample, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"header": sample.header}, sort_keys=True) + "\n")
        for pull in sample.rows:
            handle.write(json.dumps(pull.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")


def read_sample(path: Path) -> Sample:
    header: dict[str, Any] = {}
    rows: list[AidevPullRequest] = []
    for number, record in enumerate(_records(path), start=1):
        if "header" in record:
            if number != 1:
                raise ValueError(f"{path}:{number}: header must be the first record")
            header = record["header"]
        else:
            rows.append(AidevPullRequest(**record))  # records were written by to_dict()
    return Sample(header=header, rows=tuple(rows), shortfall=dict(header.get("shortfall", {})))


def _records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)
