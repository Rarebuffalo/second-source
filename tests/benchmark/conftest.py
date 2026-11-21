"""A fake Hugging Face dataset viewer for benchmark tests (no network)."""

import httpx
import pytest

# Like the real table, the fake one stores each agent's rows as one contiguous block,
# with Jules rare to mimic the real skew. (label, number of rows), in table order.
BLOCKS = [
    ("OpenAI_Codex", 200),
    ("Claude_Code", 400),
    ("Cursor", 100),
    ("Devin", 100),
    ("Google_Jules", 50),
    ("Copilot", 150),
]
TOTAL = sum(size for _label, size in BLOCKS)


def grouped_label(index):
    for label, size in BLOCKS:
        if index < size:
            return label
        index -= size
    raise IndexError(index)


def interleaved_label(index):
    """A table where agents are mixed row by row (not grouped)."""
    return [label for label, _size in BLOCKS][index % len(BLOCKS)]


def fake_row(index, label_for=grouped_label):
    """Row ``index`` of the fake table. Every third row is unmerged; every seventh has no body."""
    return {
        "html_url": f"https://github.com/org/repo{index % 13}/pull/{index}",
        "agent": label_for(index),
        "title": f"PR {index}",
        "body": "" if index % 7 == 0 else f"Body of PR {index}. All tests pass.",
        "state": "closed",
        "created_at": "2026-01-01T00:00:00Z",
        "merged_at": None if index % 3 == 0 else "2026-01-02T00:00:00Z",
        "id": str(100000 + index),
    }


class FakeViewer:
    """Serves /rows from a synthetic table, /filter for pr_commits (three commits per PR) and
    /api/datasets for the revision."""

    # Exposed for tests (tests cannot import conftest under --import-mode=importlib).
    BLOCKS = BLOCKS
    interleaved = staticmethod(interleaved_label)

    def __init__(self, total=TOTAL, failures=0, label_for=grouped_label):
        self.total = total
        self.label_for = label_for
        self.failures = failures  # how many requests fail with 500 before succeeding
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.failures:
            self.failures -= 1
            return httpx.Response(500)
        if request.url.path.startswith("/api/datasets/"):
            return httpx.Response(200, json={"sha": "abc123"})
        if request.url.path == "/filter":
            pr_id = request.url.params["where"].split("'")[1]
            rows = [
                {"row": {"pr_id": pr_id, "message": f"commit {n} of {pr_id}"}} for n in range(3)
            ]
            return httpx.Response(200, json={"rows": rows, "num_rows_total": 3})
        offset = int(request.url.params["offset"])
        length = int(request.url.params["length"])
        rows = [
            {"row_idx": i, "row": fake_row(i, self.label_for)}
            for i in range(offset, min(offset + length, self.total))
        ]
        return httpx.Response(200, json={"rows": rows, "num_rows_total": self.total})


@pytest.fixture
def viewer():
    return FakeViewer
