"""Tests for reading AIDev from a downloaded Parquet file (benchmark/aidev_local.py)."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from second_source.benchmark.aidev import AidevError
from second_source.benchmark.aidev_local import ParquetAidev
from second_source.benchmark.sample import draw_sample, locate_blocks


def write_table(path, labels):
    rows = [
        {
            "id": 1000 + n,
            "html_url": f"https://github.com/org/repo{n % 7}/pull/{n}",
            "agent": label,
            "title": f"PR {n}",
            "body": f"Body {n}. All tests pass.",
            "state": "closed",
            "created_at": "2026-01-01T00:00:00Z",
            "merged_at": "2026-01-02T00:00:00Z",
            "number": n,  # extra columns are ignored
        }
        for n, label in enumerate(labels)
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)


@pytest.fixture
def table(tmp_path):
    path = tmp_path / "pull_request.parquet"
    write_table(path, ["OpenAI_Codex"] * 40 + ["Devin"] * 30 + ["Copilot"] * 30)
    return path


def test_pages_map_rows_like_the_api(table):
    page = ParquetAidev(table).page(38, 4)
    assert page.total == 100
    assert [row.agent for row in page.rows] == ["codex", "codex", "devin", "devin"]
    first = page.rows[0]
    assert (first.url, first.id, first.merged) == (
        "https://github.com/org/repo3/pull/38",
        "1038",
        True,
    )


def test_blocks_and_samples_work_on_the_local_table(table):
    source = ParquetAidev(table)
    blocks = locate_blocks(source)
    assert [(b.agent, b.start, b.end) for b in blocks] == [
        ("codex", 0, 40),
        ("devin", 40, 70),
        ("copilot", 70, 100),
    ]
    sample = draw_sample(source, per_agent=3, seed=1, max_per_repo=1)
    assert len(sample.rows) == 9
    assert sample.header["revision"].startswith("parquet-sha256:")


def test_revision_identifies_the_exact_file(table, tmp_path):
    other = tmp_path / "other.parquet"
    write_table(other, ["Devin"] * 5)
    assert ParquetAidev(table).revision() == ParquetAidev(table).revision()
    assert ParquetAidev(table).revision() != ParquetAidev(other).revision()


def test_invalid_pages_and_files_are_errors(table, tmp_path):
    with pytest.raises(ValueError):
        ParquetAidev(table).page(0, 101)
    broken = tmp_path / "broken.parquet"
    broken.write_text("not parquet")
    with pytest.raises(AidevError):
        ParquetAidev(broken)
