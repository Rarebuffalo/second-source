"""Tests for the AIDev dataset viewer client (benchmark/aidev.py)."""

import httpx
import pytest

from second_source.benchmark.aidev import AidevClient, AidevError, AidevPullRequest


def client_for(handler, retries=3):
    return AidevClient(
        transport=httpx.MockTransport(handler), retries=retries, sleep=lambda _s: None
    )


def test_page_maps_rows_and_agent_names(viewer):
    fake = viewer()
    page = client_for(fake).page(0, 10)
    assert page.total == 1000
    assert len(page.rows) == 10
    first = page.rows[0]
    assert first.agent == "claude_code"  # "Claude_Code" mapped to our detection name
    assert {row.agent for row in page.rows} == {
        "claude_code",
        "copilot",
        "codex",
        "cursor",
        "devin",
        "jules",
    }
    params = fake.requests[0].url.params
    assert params["dataset"] == "hao-li/AIDev" and params["config"] == "pull_request"


def test_unknown_agent_label_is_kept_verbatim():
    row = {"html_url": "u", "agent": "NewAgent", "body": None, "title": None}
    pull = AidevPullRequest.from_row(row)
    assert pull.agent == "NewAgent"
    assert pull.body == "" and not pull.merged


def test_rejects_invalid_pages(viewer):
    client = client_for(viewer())
    with pytest.raises(ValueError):
        client.page(0, 101)
    with pytest.raises(ValueError):
        client.page(-1)


def test_retries_server_errors_then_succeeds(viewer):
    fake = viewer(failures=2)
    assert len(client_for(fake).page(0, 5).rows) == 5
    assert len(fake.requests) == 3


def test_gives_up_after_retries(viewer):
    with pytest.raises(AidevError, match="HTTP 500"):
        client_for(viewer(failures=10), retries=1).page(0)


def test_loading_index_error_is_retried():
    answers = [{"error": "the dataset index is loading"}, {"rows": [], "num_rows_total": 0}]
    client = client_for(lambda _request: httpx.Response(200, json=answers.pop(0)))
    assert client.page(0).total == 0


def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)

    with pytest.raises(AidevError, match="HTTP 404"):
        client_for(handler).page(0)
    assert len(calls) == 1


def test_unexpected_shape_is_an_error():
    client = client_for(lambda _request: httpx.Response(200, json={"rows": "nope"}))
    with pytest.raises(AidevError, match="unexpected response shape"):
        client.page(0)


def test_revision(viewer):
    assert client_for(viewer()).revision() == "abc123"
