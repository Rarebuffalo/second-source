<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.png">
  <img src="docs/assets/logo-light.png" width="340" alt="Second Source">
</picture>
<br/><br/>

[![PyPI](https://img.shields.io/pypi/v/second-source)](https://pypi.org/project/second-source/)
[![CI](https://github.com/Rarebuffalo/second-source/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Rarebuffalo/second-source/actions/workflows/ci.yml)
[![GitHub Marketplace](https://img.shields.io/badge/Marketplace-Second%20Source%20Claim%20Check-2ea44f?logo=github)](https://github.com/marketplace/actions/second-source-claim-check)
[![License](https://img.shields.io/github/license/Rarebuffalo/second-source)](LICENSE)

Second Source checks the claims that AI coding agents make in pull requests against GitHub's
own record. When an agent writes "all tests pass" or "Fixes #123", Second Source looks at the
CI jobs on that commit, the files the PR changed, the issue's state and the branch's history,
and reports which claims hold up. It is deterministic: rules decide every verdict, and no
language model reads the PR.

It runs as a GitHub Action on every agent pull request, or as a command-line tool on any public
PR. It supports Claude Code, Codex, Copilot, Cursor, Devin and Jules.

```text
$ uvx second-source check https://github.com/Rarebuffalo/second-source-sandbox/pull/31

Second Source · Rarebuffalo/second-source-sandbox#31 · https://github.com/Rarebuffalo/second-source-sandbox/pull/31
Agent: claude_code (detected by body_marker: Generated with [Claude Code])
Claims: 1 pending, 4 verified

… pending      Fixes #21
    said in body: "Closes #21."
    why: decided once the PR is merged
    - coding.fixes_issue: Merged: False; issue still open: True
    - coding.fixes_issue_durable: Watched for 30 days after merge
✔ verified     Docs updated (low confidence)
    said in commit: "README updated."
    why: documentation files changed
    - coding.docs_updated: 1 documentation file(s) changed
✔ verified     Tests added
    said in body: "Added to `tests/test_core.py`:"
    why: test files changed
    - coding.tests_added: 1 test file(s) added or changed
✔ verified     Tests pass
    said in body: "Local results: `pytest` 13 passed;"
    why: every matching CI job passed
    - coding.ci_result: 1 of 1 matching CI jobs passed
✔ verified     Lint passes (low confidence)
    said in body: "`ruff check .` passed."
    why: every matching CI job passed
    - coding.ci_result: 1 of 1 matching CI jobs passed

GitHub API requests: 9
```

> [!NOTE]
> Second Source is in beta (0.5). Verdicts are advice by default and never block a merge.
> Interfaces may change before 1.0.

## Quickstart

Add the Action to a repository. From the repository's root:

```bash
uvx second-source init
```

This writes `.github/workflows/second-source.yml`, with your CI workflows already listed so
verdicts update when CI finishes. Commit it, and the next agent pull request gets a
"Second Source" check. The [GitHub Action reference](docs/action.md) shows the workflow in full
and explains each input and permission.

Check a single pull request from the command line, with
[uv](https://docs.astral.sh/uv/) installed:

```bash
uvx second-source check https://github.com/OWNER/REPO/pull/123
```

## What it checks

| Claim | Checked against |
|---|---|
| Tests pass, build succeeds, lint passes | CI jobs on the commit the claim refers to |
| Tests added, docs updated | The files the pull request changed |
| Fixes an issue | The issue was closed by the merge, and stays closed for 30 days |
| Deployed | GitHub deployment records for the commit |
| The merge holds (every merged PR) | No revert of the merge within 30 days |

Each claim is verified, contradicted, pending or unverifiable. Every check also ends with the
evidence itself (CI on the head commit, what the change touched, merge and revert state), so it
is useful on pull requests that make no claims. See [Claims and verdicts](docs/claims.md).

## Properties

- **Missing evidence is never a contradiction.** A claim is contradicted only when GitHub's
  record clearly disagrees with it, for example when the test job failed on that commit. When
  evidence is missing, expired or ambiguous, the verdict is unverifiable.
- **Deterministic.** Claims are found with rules whose precision is measured on a labelled set
  of real agent pull requests, and checks are small YAML files with [CEL](https://cel.dev)
  expressions. The same evidence always gives the same verdict.
- **Checked while the evidence exists.** GitHub deletes old CI runs, so a claim that is not
  checked when the pull request is made often cannot be checked later. The Action checks at that
  moment and keeps watching merged claims for reverts and reopened issues.
- **Hard to game.** A verification is withdrawn when the same pull request skips tests, weakens
  how CI runs them or lowers a coverage gate.
- **Safe on `pull_request_target`.** The Action never checks out or runs pull request code,
  treats all PR text as untrusted, and needs no secrets. See the
  [security model](docs/security-model.md).

## How it works

```text
pull request ─> detect agent ─> extract claims ─> fetch the evidence those claims need
             ─> run checks (YAML + CEL) ─> verdicts ─> check run, CLI report or JSON
```

The engine knows nothing about code: claims, evidence and checks are generic, and a coding pack
supplies the coding-specific rules. [How it works](docs/how-it-works.md) explains each step.

## Accuracy

Claim extraction is measured on 402 real agent pull requests from the
[AIDev](https://huggingface.co/datasets/hao-li/AIDev) dataset, half of them held out. On the
held-out half, precision is 1.00 for issue references and lint claims and 0.88 for "tests pass"
claims. In a benchmark of 1,175 merged agent pull requests, 2 of the 385 that could still be
checked had a claim that GitHub's record contradicts; most older "tests pass" claims could no
longer be checked because GitHub had deleted the CI runs. [Accuracy](docs/accuracy.md) covers
the method, its limits and the open data.

## Documentation

- New to Second Source: [Quickstart](docs/quickstart.md), then the [FAQ](docs/faq.md)
- Using the Action: [GitHub Action reference](docs/action.md) and
  [Troubleshooting](docs/troubleshooting.md)
- Using the CLI: [CLI reference](docs/cli.md)
- Reading results: [Claims and verdicts](docs/claims.md)
- Security reviewers: [Security model](docs/security-model.md) and [SECURITY.md](SECURITY.md)
- Researchers: [Accuracy](docs/accuracy.md) and the [benchmark method](docs/benchmark.md)
- Contributors: [CONTRIBUTING.md](CONTRIBUTING.md) and the [architecture](docs/ARCHITECTURE.md)

The full index is in [docs/](docs/README.md); release notes are in [CHANGELOG.md](CHANGELOG.md).

## License

Second Source is licensed under [Apache-2.0](LICENSE). The golden set and the benchmark use the
AIDev dataset by Hao Li et al. ([hao-li/AIDev](https://huggingface.co/datasets/hao-li/AIDev)),
licensed CC-BY-4.0; the PR texts in the golden set remain under that licence (see
[NOTICE](NOTICE)).
