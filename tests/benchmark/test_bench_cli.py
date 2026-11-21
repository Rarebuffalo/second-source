"""End-to-end tests for ``second-source bench`` with fake AIDev and fake GitHub APIs."""

import io
import json

import httpx
import yaml

from second_source.benchmark.aidev import AidevClient, AidevPullRequest
from second_source.benchmark.sample import Sample, write_sample
from second_source.cli.main import main
from second_source.sources.github.auth import TokenResult
from second_source.sources.github.client import GitHubClient


def bench(argv, *, viewer=None, github=None, token=None):
    out, err = io.StringIO(), io.StringIO()
    tokens = []

    def client_factory(value, _use_cache):
        tokens.append(value)
        return GitHubClient(value, transport=httpx.MockTransport(github), sleep=lambda _s: None)

    code = main(
        ["bench", *argv],
        client_factory=client_factory,
        token_resolver=lambda _env: TokenResult(token, "test" if token else "none"),
        aidev_factory=lambda: AidevClient(
            transport=httpx.MockTransport(viewer), sleep=lambda _s: None, min_interval=0
        ),
        env={},
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue(), tokens


def test_sample_writes_a_reproducible_file(viewer, tmp_path):
    path = tmp_path / "s.jsonl"
    code, out, _err, _tokens = bench(
        ["sample", "--per-agent", "2", "--seed", "1", "-o", str(path)], viewer=viewer()
    )
    assert code == 0 and "wrote 12 pull requests" in out
    header = json.loads(path.read_text().splitlines()[0])["header"]
    assert header["filter"] == "merged_with_body" and header["seed"] == 1


def test_sample_reports_unknown_agents(viewer, tmp_path):
    code, _out, err, _tokens = bench(
        [
            "sample",
            "--per-agent",
            "1",
            "--seed",
            "1",
            "--agents",
            "nobody",
            "-o",
            str(tmp_path / "s"),
        ],
        viewer=viewer(),
    )
    assert code == 2 and "not in the dataset" in err


def write_one_pr_sample(tmp_path):
    pull = AidevPullRequest(
        "https://github.com/Octo/App/pull/7", "codex", "t", "b", "closed", None, "2026-11-20"
    )
    path = tmp_path / "s.jsonl"
    write_sample(Sample(header={"seed": 1}, rows=(pull,)), path)
    return path


def test_run_then_summarize(fake_github, tmp_path):
    sample = write_one_pr_sample(tmp_path)
    results = tmp_path / "r.jsonl"
    code, out, err, tokens = bench(
        ["run", str(sample), "-o", str(results), "--anonymous"], github=fake_github(), token="t"
    )
    assert code == 0 and "checked 1, errors 0" in out
    assert tokens == [None]  # --anonymous wins over an available token
    assert "no GitHub token" in err

    code, out, _err, _tokens = bench(["summarize", str(results), "-o", str(tmp_path / "sum")])
    assert code == 0
    summary = json.loads((tmp_path / "sum" / "summary.json").read_text())
    assert summary["agents"]["codex"]["prs"] == 1
    assert "## codex" in (tmp_path / "sum" / "summary.md").read_text()


def test_run_uses_an_available_token(fake_github, tmp_path):
    sample = write_one_pr_sample(tmp_path)
    _code, _out, err, tokens = bench(
        ["run", str(sample), "-o", str(tmp_path / "r.jsonl")], github=fake_github(), token="t"
    )
    assert tokens == ["t"] and "no GitHub token" not in err


def test_golden_scores_a_split(tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({"url": "u", "body": "All tests pass.", "expected": ["TESTS_PASS"]}) + "\n"
    )
    code, out, _err, _tokens = bench(["golden", str(cases), "--split", "all"])
    assert code == 0 and "1 cases (all)" in out and "TESTS_PASS" in out


def test_sample_can_exclude_urls_from_earlier_files(viewer, tmp_path):
    first = tmp_path / "first.jsonl"
    # Claude Code has the largest block in the fake table, so the draw budget is never the limit.
    common = ["sample", "--per-agent", "3", "--seed", "1", "--agents", "claude_code"]
    bench([*common, "-o", str(first)], viewer=viewer())
    second = tmp_path / "second.jsonl"
    args = [*common, "--exclude-from", str(first)]
    code, _out, _err, _tokens = bench([*args, "-o", str(second)], viewer=viewer())
    urls = [json.loads(line).get("url") for line in first.read_text().splitlines()[1:]]
    again = [json.loads(line).get("url") for line in second.read_text().splitlines()[1:]]
    assert code == 0 and len(again) == 3 and not set(urls) & set(again)


def test_merge_labels_reports_agreement_and_unresolved_cases(tmp_path):
    sample = write_one_pr_sample(tmp_path)
    url = "https://github.com/Octo/App/pull/7"
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(json.dumps({"url": url, "expected": ["TESTS_PASS"]}) + "\n")
    b.write_text(json.dumps({"url": url, "expected": []}) + "\n")
    out = tmp_path / "cases.jsonl"
    argv = ["merge-labels", str(sample), "--a", str(a), "--b", str(b), "-o", str(out)]
    code, text, _err, _tokens = bench(argv)
    assert code == 0 and "exact agreement 0/1" in text and "unresolved 1" in text

    adjudications = tmp_path / "adj.json"
    adjudications.write_text(json.dumps({url: {"expected": [], "reason": "no result stated"}}))
    code, text, _err, _tokens = bench([*argv, "--adjudications", str(adjudications)])
    assert "unresolved 0" in text
    assert json.loads(out.read_text())["adjudication"] == "no result stated"


def test_rules_prints_per_rule_precision(tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({"url": "u", "body": "All tests pass.", "expected": ["TESTS_PASS"]}) + "\n"
    )
    code, out, _err, _tokens = bench(["rules", str(cases), "--split", "all"])
    assert code == 0 and "tests_pass.plain" in out


def test_rules_select_writes_a_trusted_rules_file(tmp_path):
    cases = tmp_path / "cases.jsonl"
    record = {"url": "u", "body": "Tests pass.", "expected": ["TESTS_PASS"]}
    cases.write_text("".join(json.dumps({**record, "url": f"u{n}"}) + "\n" for n in range(5)))
    out = tmp_path / "trusted.yaml"
    argv = ["rules", str(cases), "--split", "all", "--select", str(out)]
    code, text, _err, _tokens = bench(argv)
    document = yaml.safe_load(out.read_text())
    assert code == 0 and "wrote 1 trusted rules" in text
    assert document["trusted"][0]["rule"] == "tests_pass.plain"
    assert document["selected_on"]["min_hits"] == 5


def test_golden_can_count_only_high_confidence_claims(tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({"url": "u", "body": "The build succeeds.", "expected": ["BUILD_OK"]}) + "\n"
    )
    _code, out, _err, _tokens = bench(["golden", str(cases), "--split", "all", "--high-confidence"])
    assert "BUILD_OK      precision=1.00 recall=0.00" in out


def test_sample_can_cap_repositories_and_exclude_earlier_repositories(viewer, tmp_path):
    first = tmp_path / "first.jsonl"
    common = ["sample", "--per-agent", "3", "--seed", "1", "--agents", "claude_code"]
    bench([*common, "-o", str(first)], viewer=viewer())
    second = tmp_path / "second.jsonl"
    args = [*common, "--exclude-repos-from", str(first), "--max-per-repo", "1"]
    code, _out, _err, _tokens = bench([*args, "-o", str(second)], viewer=viewer())

    def repos(path):
        rows = [json.loads(line) for line in path.read_text().splitlines()[1:]]
        return [row["url"].split("/")[4] for row in rows]

    assert code == 0
    again = repos(second)
    assert len(again) == len(set(again)) == 3  # one per repository
    assert not set(repos(first)) & set(again)  # no repository seen before


def test_commits_adds_commit_messages_for_labelling(viewer, tmp_path):
    sample = tmp_path / "s.jsonl"
    bench(["sample", "--per-agent", "1", "--seed", "1", "-o", str(sample)], viewer=viewer())
    out = tmp_path / "commits.jsonl"
    code, stdout, _err, _tokens = bench(
        ["commits", str(sample), "--per-pr", "2", "-o", str(out)], viewer=viewer()
    )
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert code == 0 and f"{len(records)} PRs" in stdout and records
    assert all(len(record["commits"]) == 2 for record in records)
    assert records[0]["commits"][0].startswith("commit 0 of ")
