"""``second-source`` command line.

    second-source check https://github.com/owner/repo/pull/123
    second-source check owner/repo#123 --json
    second-source bench ...   (the benchmark; see cli/bench.py)

Zero setup: a token is picked up from GITHUB_TOKEN / GH_TOKEN or the GitHub CLI if present;
without one the public API is used (60 requests per hour, enough for a few PRs).

Exit codes (useful in scripts and CI):
    0  checked; no claim was contradicted
    1  checked; at least one claim was contradicted
    2  could not check (bad reference, PR not found, network or API error)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from second_source import __version__
from second_source.benchmark.aidev import AidevClient
from second_source.cli import bench
from second_source.core.model import Result
from second_source.pipeline import check_pull_request
from second_source.report.render import to_json, to_text
from second_source.sources.github.auth import TokenResult, resolve_token
from second_source.sources.github.cache import DiskCache
from second_source.sources.github.client import GitHubClient, GitHubError
from second_source.sources.github.source import GitHubSource, PullRequestNotFound
from second_source.sources.github.urls import parse_pull_request

EXIT_OK = 0
EXIT_CONTRADICTED = 1
EXIT_ERROR = 2

ClientFactory = Callable[[str | None, bool], GitHubClient]
TokenResolver = Callable[[Mapping[str, str] | None], TokenResult]


def default_client_factory(token: str | None, use_cache: bool) -> GitHubClient:
    return GitHubClient(token, cache=DiskCache() if use_cache else None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="second-source",
        description="Check whether an AI agent's claims in a pull request are true.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="check the claims in one pull request")
    check.add_argument("pull_request", help="PR URL or owner/repo#number")
    check.add_argument("--json", action="store_true", help="print machine-readable JSON")
    check.add_argument("--no-cache", action="store_true", help="do not use the on-disk cache")
    check.add_argument("--no-color", action="store_true", help="disable coloured output")
    check.add_argument(
        "--anonymous",
        action="store_true",
        help="use the public API without any token, even if one is available",
    )
    bench.add_parser(commands)
    commands.add_parser(
        "action", help="run as the GitHub Action (reads its inputs from the environment)"
    )
    init = commands.add_parser("init", help="add the Second Source workflow to a repository")
    init.add_argument("--path", type=Path, default=Path("."), help="repository root")
    init.add_argument("--force", action="store_true", help="overwrite an existing workflow")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: ClientFactory = default_client_factory,
    token_resolver: TokenResolver = resolve_token,
    aidev_factory: Callable[[], AidevClient] = AidevClient,
    env: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = build_parser().parse_args(argv)

    if args.command == "action":
        from second_source.action.main import run_action  # only needed inside Actions

        return run_action(os.environ if env is None else env, out=out)
    if args.command == "init":
        return _init(args, out, err)
    if args.command == "bench":
        context = bench.BenchContext(
            client_factory=client_factory,
            token_resolver=token_resolver,
            aidev_factory=aidev_factory,
            env=env,
            out=out,
            err=err,
        )
        return bench.run_command(args, context)
    return _check(args, client_factory, token_resolver, env, out, err)


def _check(
    args: argparse.Namespace,
    client_factory: ClientFactory,
    token_resolver: TokenResolver,
    env: Mapping[str, str] | None,
    out: TextIO,
    err: TextIO,
) -> int:
    try:
        ref = parse_pull_request(args.pull_request)
    except ValueError as error:
        print(f"error: {error}", file=err)
        return EXIT_ERROR

    token = TokenResult(None, "none") if args.anonymous else token_resolver(env)
    if token.token is None and not args.json:
        print(
            "note: no GitHub token found, using the public API (60 requests/hour). "
            "Set GITHUB_TOKEN or run `gh auth login` for more.",
            file=err,
        )

    with client_factory(token.token, not args.no_cache) as client:
        try:
            report = check_pull_request(ref, GitHubSource(client))
        except PullRequestNotFound as error:
            print(f"error: {error}", file=err)
            return EXIT_ERROR
        except GitHubError as error:
            print(f"error: could not read the pull request from GitHub: {error}", file=err)
            return EXIT_ERROR

    if args.json:
        print(json.dumps(to_json(report), indent=2), file=out)
    else:
        color = not args.no_color and _supports_color(out, env)
        print(to_text(report, color=color), file=out)

    contradicted = any(v.result is Result.CONTRADICTED for v in report.verdicts)
    return EXIT_CONTRADICTED if contradicted else EXIT_OK


def _init(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from second_source.action.setup import WorkflowExists, write_workflow

    try:
        path, names = write_workflow(args.path, force=args.force)
    except WorkflowExists as existing:
        print(f"error: {existing} already exists (use --force to overwrite)", file=err)
        return EXIT_ERROR
    print(f"wrote {path}", file=out)
    if names:
        print(f"re-checks after these CI workflows finish: {', '.join(names)}", file=out)
    else:
        print("no CI workflows found; the daily run will pick up CI results", file=out)
    print("next: commit the file and open a pull request", file=out)
    return EXIT_OK


def _supports_color(stream: TextIO, env: Mapping[str, str] | None) -> bool:
    """Colour only for real terminals, and never when NO_COLOR is set (no-color.org)."""
    environment = os.environ if env is None else env
    return stream.isatty() and "NO_COLOR" not in environment


def run() -> None:
    """Console-script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    run()
