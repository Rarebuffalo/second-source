"""Tests for the command line (cli/main.py), driven against the shared fake GitHub API."""

import io
import json

import httpx

from second_source.cli.main import EXIT_CONTRADICTED, EXIT_ERROR, EXIT_OK, main
from second_source.sources.github.auth import TokenResult
from second_source.sources.github.client import GitHubClient


def no_token(_env):
    """Token resolver for tests: never reads the real environment or the gh CLI."""
    return TokenResult(None, "none")


def invoke(fake, argv, token_resolver=no_token):
    """Run the CLI with a fake GitHub; return (exit code, stdout, stderr, tokens used)."""
    out, err = io.StringIO(), io.StringIO()
    tokens = []

    def factory(token, use_cache):
        tokens.append(token)
        return GitHubClient(token, transport=httpx.MockTransport(fake), sleep=lambda _s: None)

    code = main(
        list(argv),
        client_factory=factory,
        token_resolver=token_resolver,
        env={},
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue(), tokens


def run_cli(fake, *argv):
    """Run the CLI without any token; return (exit code, stdout, stderr)."""
    code, out, err, _tokens = invoke(fake, argv)
    return code, out, err


def has_token(_env):
    return TokenResult("real-token", "gh auth token")


def test_check_prints_a_readable_report(fake_github):
    code, out, err = run_cli(fake_github(), "check", "https://github.com/Octo/App/pull/7")
    assert code == EXIT_OK
    assert "Second Source · Octo/App#7" in out
    assert "Tests pass" in out
    assert "no GitHub token found" in err  # helpful hint, on stderr only


def test_json_output_is_valid_and_quiet(fake_github):
    code, out, err = run_cli(fake_github(), "check", "Octo/App#7", "--json")
    assert code == EXIT_OK
    assert json.loads(out)["pull_request"]["ref"] == "Octo/App#7"
    assert err == ""


def test_contradicted_claims_give_exit_code_1(fake_github):
    failing = {
        "/repos/Octo/App/commits/c1/check-runs": {
            "total_count": 1,
            "check_runs": [
                {"id": 205, "name": "pytest", "status": "completed", "conclusion": "failure"}
            ],
        }
    }
    code, out, _err = run_cli(fake_github(failing), "check", "Octo/App#7")
    assert code == EXIT_CONTRADICTED
    assert "✘ contradicted" in out


def test_invalid_reference_gives_exit_code_2(fake_github):
    code, _out, err = run_cli(fake_github(), "check", "not-a-pr")
    assert code == EXIT_ERROR
    assert "not a pull request reference" in err


def test_missing_pull_request_gives_exit_code_2(fake_github):
    code, _out, err = run_cli(fake_github(), "check", "Octo/App#99")
    assert code == EXIT_ERROR
    assert "not found" in err


def test_api_errors_give_exit_code_2(fake_github):
    code, _out, err = run_cli(
        fake_github({"/repos/Octo/App/pulls/7": httpx.Response(500)}), "check", "Octo/App#7"
    )
    assert code == EXIT_ERROR
    assert "could not read the pull request" in err


def test_no_colour_when_not_a_terminal(fake_github):
    _code, out, _err = run_cli(fake_github(), "check", "Octo/App#7")
    assert "\033[" not in out


def test_anonymous_flag_ignores_available_tokens(fake_github):
    code, _out, _err, tokens = invoke(
        fake_github(), ["check", "Octo/App#7", "--anonymous"], has_token
    )
    assert code == EXIT_OK
    assert tokens == [None]


def test_available_token_is_used_by_default(fake_github):
    _code, _out, _err, tokens = invoke(fake_github(), ["check", "Octo/App#7"], has_token)
    assert tokens == ["real-token"]
