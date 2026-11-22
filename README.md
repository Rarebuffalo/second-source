# Second Source

**Every AI claim needs a second source.**

AI agents report on their own work: *"All tests pass." "Fixes #123." "Deployed to staging."*
Second Source checks those claims independently, against evidence from the systems the agent
acted on, and keeps re-checking that they stay true.

The first domain is **coding agents**. Point it at a pull request opened by Claude Code, Codex,
Copilot, Cursor, Devin or Jules, and it tells you which of the agent's claims hold up against
CI results, changed files, issue state and Git history.

> **Status: early alpha.** The engine, the coding pack, the CLI and the GitHub Action work and
> are tested; the Action has no tagged release yet, and the public benchmark is in progress
> (see [Roadmap](#roadmap)). Interfaces may change before 1.0.

## Try it

Requires [uv](https://docs.astral.sh/uv/). Nothing to install or configure:

```bash
uvx --from git+https://github.com/Rarebuffalo/second-source second-source check https://github.com/OWNER/REPO/pull/123
```

A GitHub token is picked up automatically from `GITHUB_TOKEN`, `GH_TOKEN` or a logged-in
GitHub CLI. Without one, the public API allows about 10 PR checks per hour.

Example output:

```text
Second Source · Octo/App#7 · https://github.com/Octo/App/pull/7
Agent: codex (detected by branch_prefix: codex/)
Claims: 1 unverifiable, 2 pending, 3 verified

? unverifiable Deployed to staging (low confidence)
    said in comment: "Deployed to staging."
    why: NO_DEPLOYMENT_RECORDS
… pending      Fixes #12
    said in body: "Fixes #12"
    why: IN_WINDOW
✔ verified     Tests added
    said in body: "Added unit tests for empty input"
    why: TEST_FILES_CHANGED
✔ verified     Tests pass (low confidence)
    said in body: "- ✅ `pytest -q`"
    why: ALL_MATCHING_CI_JOBS_PASSED
```

| Option | What it does |
|---|---|
| `--json` | Machine-readable output (versioned schema) |
| `--anonymous` | Never use a token, even if one is available |
| `--no-cache` | Skip the on-disk response cache |
| `--no-color` | Plain text (colour is also off when `NO_COLOR` is set) |

Exit codes: `0` nothing contradicted, `1` at least one claim contradicted, `2` could not check.

## Use it in your repository (GitHub Action)

One command writes the workflow; commit it and every PR opened by a coding agent gets a
**Second Source** check next to your CI:

```bash
uvx --from git+https://github.com/Rarebuffalo/second-source second-source init
```

The generated `.github/workflows/second-source.yml`:

- checks agent PRs when they are opened or updated, waits a few minutes for CI, and updates the
  check when your CI workflows finish (it reads their names from `.github/workflows`);
- re-checks recent agent PRs daily, for durability (reverts, reopened issues);
- understands `/second-source recheck` and `/second-source dispute <reason>` in PR comments
  (from the PR author or people with write access).

By default the check is advice: a contradicted claim makes it *neutral*, never failing. Set
`fail-on-contradiction: "true"` to make it block merges. Other inputs: `wait-minutes`
(default 10; use 0 on private repositories to save Actions minutes), `only-agents`,
`sweep-days`. It costs nothing to run on public repositories, needs no server and no secrets,
and never checks out the PR's code. Design: [docs/action.md](docs/action.md).

## What it checks

| Claim | Example wording | Checked against |
|---|---|---|
| Tests pass | "All tests pass", "✅ `pytest`" | CI test jobs on the commit the claim refers to |
| Build succeeds | "Build succeeds", "`cargo build` ✅" | CI build jobs on that commit |
| Lint passes | "Lint passes", "no ESLint warnings" | CI lint, format and type-check jobs |
| Tests added | "Added unit tests for the parser" | Test files changed in the PR (never contradicted: tests can live in code files) |
| Docs updated | "Updated the README" | Documentation files changed in the PR (never contradicted: docs can live in code) |
| Fixes an issue | "Fixes #123", "Closes owner/repo#45" | Issue closed by the merge, and still closed later |
| Deployed | "Deployed to staging" | GitHub deployment records |
| Merge holds (implicit) | every merged agent PR | No revert of the merge within 30 days |

Each claim gets one verdict:

- **verified**: the evidence agrees.
- **contradicted**: the evidence disagrees, for example the test job failed on that commit.
- **pending**: too early to tell (CI still running, or inside the durability window).
- **unverifiable**: there is no evidence either way.

**Missing or unavailable evidence is never reported as contradicted.** A claim the text says
ran *locally* ("tests pass locally") is not contradicted by failing CI either; it gets its own
reason, `CI_FAILED_LOCAL_CLAIM`, because local and CI environments differ.

Claims are found with deterministic rules (no AI model), so results are reproducible and
cannot be steered by prompt injection in the PR text. Each claim is marked **high** or **low
confidence** depending on whether a rule with measured high precision found it.

## How accurate is it?

Claim extraction is measured on a public golden set: 402 real agent pull requests from the
[AIDev](https://huggingface.co/datasets/hao-li/AIDev) dataset, 67 per agent, each labelled by
two independent labellers (96% exact agreement). Half the set is used to tune rules; the other
half is scored only to report.

On the held-out half, precision is 1.00 for issue references and lint claims and 0.88 for
high-confidence "tests pass" claims, below our 0.95 target. Full numbers, the labelling guide
and the method: [tests/packs/coding/golden/real/](tests/packs/coding/golden/real/README.md).
Until every claim type meets the target, we treat a "contradicted" verdict as a strong signal
that a person should confirm, not as proof.

## How it works

```text
pull request ──> detect agent ──> extract claims ──> fetch only the evidence those claims need
             ──> run checks (YAML + CEL, pure and replayable) ──> verdicts ──> report
```

- **Domain-neutral core.** The engine knows nothing about code. Claims, evidence and checks
  are generic; the coding pack adds the coding-specific parts. A customer-support fixture pack
  in the tests keeps the core honest.
- **Checks are data.** Each check is a small YAML file with [CEL](https://cel.dev)
  expressions, so its logic is readable and reviewable
  ([example](src/second_source/packs/coding/checks/ci_result.yaml)).
- **Replayable.** Every check records a hash of its exact inputs; the same inputs always give
  the same verdict.

More: [architecture](docs/ARCHITECTURE.md), [coding pack](docs/packs/coding.md),
[GitHub source](docs/sources/github.md), [benchmark method](docs/benchmark.md).

## Privacy and security

- The CLI talks only to the GitHub API. Nothing is sent to us or anyone else.
- Tokens are never printed, logged or written to the cache.
- PR code is never checked out or run, which is why the Action can safely use
  `pull_request_target`.
- Report vulnerabilities privately: see [SECURITY.md](SECURITY.md).

## Roadmap

- **Agent Claim Truth benchmark.** How often each coding agent's claims hold up, with
  confidence intervals, published as open data. The pipeline is built
  (`second-source bench`); the first full run is next.
- **GitHub Action.** Built and tested (see above); next are a first tagged release (`v1`) and a
  Marketplace listing.
- **Durability checks over time.** The Action re-checks merged claims daily (reverts,
  reopened issues); a longer watch window is planned.
- **More domains.** The same core for customer-support, voice and other agents.

## Development

```bash
git clone https://github.com/Rarebuffalo/second-source && cd second-source
```

```bash
uv sync
```

```bash
./scripts/check.sh
```

`check.sh` runs ruff (lint and format) and the full test suite; it must pass before every
commit. Tests never touch the network. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Data attribution

The golden set and benchmark samples use the AIDev dataset by Hao Li et al.
([hao-li/AIDev](https://huggingface.co/datasets/hao-li/AIDev)), licensed CC-BY-4.0.

## Licence

[Apache-2.0](LICENSE). The AIDev-derived PR texts in the golden set remain under CC-BY-4.0
(see [NOTICE](NOTICE)).
