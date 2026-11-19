"""Tests for pull-request reference parsing (sources/github/urls.py)."""

import pytest

from second_source.sources.github.urls import PullRequestRef, parse_pull_request


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/octo/app/pull/7",
        "https://github.com/octo/app/pull/7/files",
        "https://github.com/octo/app/pull/7#issuecomment-1",
        "http://www.github.com/octo/app/pull/7",
        "github.com/octo/app/pull/7",
        "  octo/app#7  ",
        "octo/app/pull/7",
    ],
)
def test_accepted_forms(text):
    assert parse_pull_request(text) == PullRequestRef("octo", "app", 7)


def test_names_with_dots_dashes_and_underscores():
    ref = parse_pull_request("my-org/my_repo.js#12")
    assert (ref.owner, ref.repo, ref.number) == ("my-org", "my_repo.js", 12)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "octo/app",
        "https://github.com/octo/app/issues/7",
        "https://gitlab.com/octo/app/pull/7",
        "https://github.com/octo/app/pull/abc",
        "octo/app#0",
        "octo/..#3",
        "https://github.com.evil.example/octo/app/pull/7",
    ],
)
def test_rejected_forms(text):
    with pytest.raises(ValueError):
        parse_pull_request(text)


def test_formatting():
    ref = PullRequestRef("octo", "app", 7)
    assert str(ref) == "octo/app#7"
    assert ref.url == "https://github.com/octo/app/pull/7"
    assert ref.full_name == "octo/app"
