"""Tests for the AIDev dataset viewer client (benchmark/aidev.py)."""

import httpx
import pytest

from second_source.benchmark.aidev import AidevClient, AidevError, AidevPullRequest


def client_for(handler, retries=3, sleeps=None, clock=lambda: 0.0, min_interval=1.0):
    return AidevClient(
        transport=httpx.MockTransport(handler),
        retries=retries,
        min_interval=min_interval,
        sleep=(sleeps.append if sleeps is not None else lambda _s: None),
        clock=clock,
    )


def test_page_maps_rows_and_agent_names(viewer):
    fake = viewer()
    page = client_for(fake).page(195, 10)  # crosses the Codex → Claude Code boundary
    assert page.total == 1000
    assert len(page.rows) == 10
    # AIDev labels are mapped to our detection names.
    assert [row.agent for row in page.rows] == ["codex"] * 5 + ["claude_code"] * 5
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


def test_rate_limits_back_off_longer_than_server_errors():
    answers = [httpx.Response(429), httpx.Response(429), httpx.Response(500)]
    answers.append(httpx.Response(200, json={"rows": [], "num_rows_total": 0}))
    sleeps = []
    client = client_for(lambda _request: answers.pop(0), sleeps=sleeps, min_interval=0)
    client.page(0)
    # 429 waits 30s then doubles; the following server error restarts from 2s (third retry: 8s).
    assert sleeps == [30.0, 60.0, 8.0]


def test_requests_are_paced(viewer):
    times = iter([0.0, 0.25, 5.0])
    sleeps = []
    client = client_for(viewer(), sleeps=sleeps, clock=lambda: next(times))
    client.page(0, 1)  # first request: no wait
    client.page(1, 1)  # 0.25s later: waits the remaining 0.75s
    client.page(2, 1)  # long after: no wait
    assert sleeps == [0.75]


def test_rows_are_redacted_before_they_are_stored():
    fake_key = "ASIA" + "EXAMPLEFAKE00003"
    body = f"![shot](https://b.s3.amazonaws.com/x.png?X-Amz-Credential={fake_key}%2F2025)"
    pull = AidevPullRequest.from_row({"html_url": "u", "agent": "Devin", "body": body})
    assert fake_key not in pull.body
    assert "X-Amz-Credential=REDACTED" in pull.body
