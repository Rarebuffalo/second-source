"""Shared test fixtures: a fake GitHub API serving one realistic agent pull request.

Used by the source, pipeline and CLI tests.

Scenario (repository Octo/App, PR #7, opened by Codex on branch codex/fix-parser):
- body claims: unit tests added, fixes #12, and a Testing section with ✅ pytest and ✅ ruff
- commits: c1 (before the PR was opened) and c2 (after)
- CI on c1: pytest failed on run 100, then passed on re-run 205 (filter=latest returns 205);
  ruff passed
- files: src/parser.py modified, tests/test_parser.py added
- issue #12 closed seconds after the merge; the PR was merged at 12:00
- the agent later commented "Deployed to staging" (no GitHub deployment records exist)
"""

import httpx
import pytest

BODY = """## Summary
- Fix the parser for empty input
- Added unit tests for empty input

Fixes #12

## Testing
- ✅ `pytest -q`
- ✅ `ruff check .`
"""

ROUTES = {
    "/repos/Octo/App/pulls/7": {
        "number": 7,
        "title": "Fix parser edge case",
        "body": BODY,
        "state": "closed",
        "created_at": "2026-11-20T09:00:00Z",
        "closed_at": "2026-11-20T12:00:00Z",
        "merged_at": "2026-11-20T12:00:00Z",
        "merge_commit_sha": "abc1234def",
        "html_url": "https://github.com/Octo/App/pull/7",
        "user": {"login": "someone"},
        "head": {"ref": "codex/fix-parser", "sha": "c2"},
        "base": {"ref": "main", "repo": {"full_name": "Octo/App"}},
    },
    "/repos/Octo/App/pulls/7/commits": [
        {
            "sha": "c1",
            "commit": {"message": "Fix parser", "committer": {"date": "2026-11-20T08:50:00Z"}},
        },
        {"sha": "c2", "commit": {"message": "Tidy", "committer": {"date": "2026-11-20T09:30:00Z"}}},
    ],
    "/repos/Octo/App/issues/7/comments": [
        {
            "user": {"login": "someone"},
            "created_at": "2026-11-20T10:00:00Z",
            "body": "Deployed to staging.",
        },
        {
            "user": {"login": "reviewer"},
            "created_at": "2026-11-20T10:05:00Z",
            "body": "All tests pass?",
        },
    ],
    "/repos/Octo/App/commits/c1/check-runs": {
        "total_count": 2,
        "check_runs": [
            {"id": 205, "name": "pytest", "status": "completed", "conclusion": "success"},
            {"id": 206, "name": "ruff", "status": "completed", "conclusion": "success"},
        ],
    },
    "/repos/Octo/App/commits/c1/status": {"state": "success", "statuses": []},
    "/repos/Octo/App/pulls/7/files": [
        {"filename": "src/parser.py", "status": "modified", "additions": 4, "deletions": 1},
        {"filename": "tests/test_parser.py", "status": "added", "additions": 30, "deletions": 0},
    ],
    "/repos/Octo/App/issues/12": {
        "number": 12,
        "state": "closed",
        "closed_at": "2026-11-20T12:00:05Z",
    },
    "/repos/Octo/App/issues/12/events": [],
    "/repos/Octo/App/commits": [],
    "/repos/Octo/App/deployments": [],
}


class FakeGitHub:
    """Serves ROUTES (plus overrides); unknown paths return 404. Records requested paths and the
    full requests (for tests that check query parameters)."""

    # Exposed for tests that tweak one response (tests cannot import conftest directly).
    BASE_ROUTES = ROUTES

    def __init__(self, overrides=None):
        self.routes = {**ROUTES, **(overrides or {})}
        self.requested = []
        self.requests = []

    def __call__(self, request):
        path = request.url.path
        self.requested.append(path)
        self.requests.append(request)
        response = self.routes.get(path)
        if isinstance(response, httpx.Response):
            return response
        if response is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json=response)


@pytest.fixture
def fake_github():
    """Factory: ``fake_github(overrides)`` returns a FakeGitHub handler."""
    return FakeGitHub
