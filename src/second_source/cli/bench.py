"""``second-source bench``: reproduce the Agent Claim Truth benchmark (docs/benchmark.md).

    second-source bench sample  --per-agent 200 --seed 1 --filter merged_with_body -o s.jsonl
    second-source bench run     s.jsonl -o results.jsonl [--limit 10] [--anonymous]
    second-source bench summarize results.jsonl -o summary/
    second-source bench golden  cases.jsonl [--split dev|test|all]

Each step reads the previous step's file, so a run can be stopped and resumed at any point.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from second_source.benchmark import golden, stats
from second_source.benchmark.aidev import AidevClient, AidevError
from second_source.benchmark.runner import read_results, run_benchmark
from second_source.benchmark.sample import FILTERS, draw_sample, read_sample, write_sample
from second_source.sources.github.auth import TokenResult
from second_source.sources.github.client import GitHubClient
from second_source.sources.github.source import GitHubSource

EXIT_OK = 0
EXIT_ERROR = 2


@dataclass(frozen=True)
class BenchContext:
    """Everything the bench commands touch outside the process, injectable for tests."""

    client_factory: Callable[[str | None, bool], GitHubClient]
    token_resolver: Callable[[Mapping[str, str] | None], TokenResult]
    aidev_factory: Callable[[], AidevClient]
    env: Mapping[str, str] | None
    out: TextIO
    err: TextIO


def add_parser(commands: argparse._SubParsersAction) -> None:
    bench = commands.add_parser("bench", help="run the Agent Claim Truth benchmark")
    steps = bench.add_subparsers(dest="bench_command", required=True)

    sample = steps.add_parser("sample", help="draw an agent-stratified sample from AIDev")
    sample.add_argument("--per-agent", type=int, required=True)
    sample.add_argument("--seed", type=int, required=True)
    sample.add_argument("--filter", choices=sorted(FILTERS), default="merged_with_body")
    sample.add_argument("--agents", help="comma-separated agent names (default: all)")
    sample.add_argument("-o", "--output", type=Path, required=True)

    run = steps.add_parser("run", help="check every sampled PR (resumable)")
    run.add_argument("sample", type=Path)
    run.add_argument("-o", "--output", type=Path, required=True)
    run.add_argument("--limit", type=int, help="check at most this many new PRs")
    run.add_argument("--anonymous", action="store_true", help="never use a GitHub token")
    run.add_argument("--no-cache", action="store_true", help="do not use the on-disk cache")

    summarize = steps.add_parser("summarize", help="write summary.json and summary.md")
    summarize.add_argument("results", type=Path)
    summarize.add_argument("-o", "--output", type=Path, required=True, help="output directory")

    score = steps.add_parser("golden", help="score extraction against a labelled golden set")
    score.add_argument("cases", type=Path)
    score.add_argument("--split", choices=[golden.DEV, golden.TEST, "all"], default=golden.DEV)


def run_command(args: argparse.Namespace, context: BenchContext) -> int:
    handlers = {
        "sample": _sample,
        "run": _run,
        "summarize": _summarize,
        "golden": _golden,
    }
    return handlers[args.bench_command](args, context)


def _sample(args: argparse.Namespace, context: BenchContext) -> int:
    agents = [a.strip() for a in args.agents.split(",")] if args.agents else None
    try:
        with context.aidev_factory() as client:
            sample = draw_sample(
                client,
                per_agent=args.per_agent,
                seed=args.seed,
                filter_name=args.filter,
                agents=agents,
            )
    except (AidevError, ValueError) as error:
        print(f"error: {error}", file=context.err)
        return EXIT_ERROR
    write_sample(sample, args.output)
    print(f"wrote {len(sample.rows)} pull requests to {args.output}", file=context.out)
    if sample.shortfall:
        print(f"note: not enough matching rows for {sample.shortfall}", file=context.err)
    return EXIT_OK


def _run(args: argparse.Namespace, context: BenchContext) -> int:
    rows = read_sample(args.sample).rows
    token = TokenResult(None, "none") if args.anonymous else context.token_resolver(context.env)
    if token.token is None:
        print(
            "note: no GitHub token, so only about 10 PRs per hour can be checked", file=context.err
        )
    with context.client_factory(token.token, not args.no_cache) as client:
        summary = run_benchmark(
            rows,
            args.output,
            source=GitHubSource(client),
            limit=args.limit,
            progress=lambda message: print(message, file=context.err),
        )
    print(
        f"checked {summary.checked}, errors {summary.errors}, "
        f"already done {summary.skipped}"
        + (f"; stopped: {summary.stopped}" if summary.stopped else ""),
        file=context.out,
    )
    return EXIT_OK


def _summarize(args: argparse.Namespace, context: BenchContext) -> int:
    summary = stats.summarize(read_results(args.results))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output / "summary.md").write_text(stats.to_markdown(summary), encoding="utf-8")
    print(f"wrote summary.json and summary.md to {args.output}", file=context.out)
    return EXIT_OK


def _golden(args: argparse.Namespace, context: BenchContext) -> int:
    cases = golden.load_cases(args.cases)
    if args.split != "all":
        cases = golden.in_split(cases, args.split)
    print(f"{len(cases)} cases ({args.split})", file=context.out)
    print(golden.score(cases).report(), file=context.out)
    return EXIT_OK
