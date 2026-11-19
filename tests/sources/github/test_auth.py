"""Tests for token discovery (sources/github/auth.py). No real subprocess is run."""

import subprocess

from second_source.sources.github.auth import resolve_token


def gh_returns(stdout="", returncode=0):
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess(["gh"], returncode, stdout=stdout, stderr="")

    return run


def gh_missing(*_args, **_kwargs):
    raise FileNotFoundError("gh")


def test_github_token_wins():
    result = resolve_token({"GITHUB_TOKEN": "t1", "GH_TOKEN": "t2"}, run=gh_returns("t3"))
    assert (result.token, result.origin) == ("t1", "GITHUB_TOKEN")


def test_gh_token_env_is_second():
    result = resolve_token({"GH_TOKEN": "t2"}, run=gh_returns("t3"))
    assert (result.token, result.origin) == ("t2", "GH_TOKEN")


def test_falls_back_to_gh_cli():
    result = resolve_token({}, run=gh_returns("t3\n"))
    assert (result.token, result.origin) == ("t3", "gh auth token")


def test_logged_out_gh_means_no_token():
    assert resolve_token({}, run=gh_returns("", returncode=1)).origin == "none"


def test_missing_gh_means_no_token():
    result = resolve_token({}, run=gh_missing)
    assert (result.token, result.origin) == (None, "none")


def test_hanging_gh_means_no_token():
    def hang(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("gh", 5)

    assert resolve_token({}, run=hang).origin == "none"


def test_blank_env_values_are_ignored():
    assert resolve_token({"GITHUB_TOKEN": "  "}, run=gh_missing).origin == "none"


def test_repr_never_shows_the_token():
    result = resolve_token({"GITHUB_TOKEN": "very-secret"}, run=gh_missing)
    assert "very-secret" not in repr(result)
