"""A fake Hugging Face dataset viewer for benchmark tests (no network)."""

import httpx
import pytest

# Agent labels as AIDev writes them; the fake table cycles through these in order,
# with Jules rare (one row in ten) to mimic the real skew.
LABELS = ["Claude_Code"] * 4 + ["Copilot"] * 2 + ["OpenAI_Codex", "Cursor", "Devin", "Google_Jules"]


def fake_row(index):
    """Row ``index`` of the fake table. Every third row is unmerged; every seventh has no body."""
    return {
        "html_url": f"https://github.com/org/repo{index % 13}/pull/{index}",
        "agent": LABELS[index % len(LABELS)],
        "title": f"PR {index}",
        "body": "" if index % 7 == 0 else f"Body of PR {index}. All tests pass.",
        "state": "closed",
        "created_at": "2026-01-01T00:00:00Z",
        "merged_at": None if index % 3 == 0 else "2026-01-02T00:00:00Z",
    }


class FakeViewer:
    """Serves /rows from a synthetic table and /api/datasets for the revision."""

    def __init__(self, total=1000, failures=0):
        self.total = total
        self.failures = failures  # how many requests fail with 500 before succeeding
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.failures:
            self.failures -= 1
            return httpx.Response(500)
        if request.url.path.startswith("/api/datasets/"):
            return httpx.Response(200, json={"sha": "abc123"})
        offset = int(request.url.params["offset"])
        length = int(request.url.params["length"])
        rows = [
            {"row_idx": i, "row": fake_row(i)}
            for i in range(offset, min(offset + length, self.total))
        ]
        return httpx.Response(200, json={"rows": rows, "num_rows_total": self.total})


@pytest.fixture
def viewer():
    return FakeViewer
