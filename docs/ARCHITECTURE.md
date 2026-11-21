# Second Source architecture

This document is for contributors. It describes how the code is organised, the contracts
between its parts, and the order in which it is being built. Sections marked *(planned)*
describe parts that do not exist yet.

## 1. What we are building

Second Source checks whether an AI agent's claims about its own work are true, using
evidence from the systems the agent acted on, and re-checks that they stay true.

The first domain is **coding agents**. In an agent-authored pull request, the agent writes
things like "All tests pass" or "Fixes #123". Second Source checks these claims against CI
results, Git history and issue state.

There are three ways to use it, and all of them share one engine:

| Surface | Who it is for | How it runs | Cost |
|---|---|---|---|
| CLI: `second-source check <PR URL>` | Anyone who wants to try it in 10 seconds | Locally via `uvx` / `pipx` | Free |
| GitHub Action *(planned)* | Repositories that want every agent PR checked | In the repo's own GitHub Actions | Free on public repos |
| Benchmark (Agent Claim Truth): `second-source bench` | Everyone comparing agents | Batch pipeline; results to be published as a static site and dataset | Free |

## 2. Design principles

1. **The core is domain-neutral.** `second_source.core` never mentions pull requests, CI,
   refunds or any industry. A test suite built around a non-coding domain (customer-support
   refunds) runs against the core to keep it honest.
2. **Evidence decides.** Verdicts come from deterministic checks over evidence. There is no AI
   model in v1. Claims are extracted with rules.
3. **Missing evidence is never guilt.** If evidence is missing or stale, the verdict is
   `UNVERIFIABLE`, never `CONTRADICTED`.
4. **The core is pure.** It has no network, no clock and no filesystem access. Time (`now`) and
   evidence are passed in. The same inputs always give the same outputs (replayable).
5. **Least effort for users.** No accounts, no secrets, no configuration by default. Results
   show up where developers already look.
6. **Nothing leaves the user's machine or runner.** The CLI and the Action talk only to the
   GitHub API. They never send data to us.
7. **Small, boring dependencies.** We use PyYAML, cel-python and an HTTP client. Anything new
   needs a reason.

## 3. Layers and packages

```
src/second_source/
  core/          domain-neutral engine (pure: no network, clock or filesystem)
    model.py       Episode, Claim, Evidence, CheckRun, Verdict, enums
    hashing.py     canonical JSON + SHA-256 for replayable inputs
    evidence.py    de-duplication of evidence records
    expr.py        CEL wrapper: value conversion, compile cache, function registry
    checks.py      check definition schema, validation, evaluation -> CheckRun
    aggregate.py   CheckRuns -> Verdict
    engine.py      Engine: registry of checks, verify(episode, claims, evidence, now)
    pack.py        pack loading (pack.yaml + check files + functions hook)
  packs/coding/  coding-agent pack
    detection.py   which agent opened a PR (author, body marker, commit trailer, branch)
    extract.py     rule-based claim extraction; every rule is named
    functions.py   CEL helpers (job and path classifiers, repo and environment matching)
    checks/        one YAML file per check
    trusted_rules.yaml  rules whose measured precision makes their claims high confidence
  sources/github/  GitHub REST client (ETag cache, pagination, rate limits), auth chain,
                   URL parsing, mappers (API JSON -> Episode, texts, Evidence), source
  pipeline.py    one PR end to end; shared by the CLI, the benchmark and the Action
  report/        renderers: terminal text, Markdown, versioned JSON
  benchmark/     AIDev sampling, golden-set scoring and label merging, per-rule precision,
                 resumable batch runner, statistics (Wilson intervals)
  action/        the GitHub Action: inputs, event -> plan, CI waiting, check-run publishing,
                 daily sweep, PR comment commands, workflow generation (docs/action.md)
  cli/           `second-source check`, `bench ...`, `action` and `init`
tests/
  core/            unit tests for the engine
  fixtures/        non-coding fixture suite (customer-support refunds)
  packs/coding/    extraction, detection and check tests; seed and real golden sets
  sources/github/  client, cache, auth, mapper and source tests against a fake API
  benchmark/       sampling, runner, statistics and bench CLI tests
  action/          Action tests against the fake API (events, publishing, waiting, end to end)
scripts/check.sh   the quality gate: ruff lint, ruff format check, full test suite
action.yml         the composite GitHub Action
```

Planned: a static benchmark site (M5).

**Dependency direction:** `core` ← `packs` ← `sources` ← `pipeline` ← `cli` / `benchmark`.
`core` imports nothing from the other packages. Packs may import `core` only.

## 4. Data flow (one pull request)

```
PR URL (or, later, an Action event)
  -> GitHubSource.load(ref)               PR, commits, comments -> texts and agent signals
  -> detect_agent(signals)                which agent wrote it (None for a human PR)
  -> extract_claims(...) + merge_claim()  Claims with spans, the commit each refers to,
                                          the rule that found it and its confidence
  -> GitHubSource.fetch_evidence(...)     only the evidence types the claims need
  -> Engine.verify(..., now=...)          CheckRuns -> Verdicts
  -> report.render: to_text / to_markdown / to_json
```

`pipeline.check_pull_request()` runs these steps. Fetching is driven by the claims: if a
PR claims nothing, it makes almost no API calls.

## 5. Core contracts

### 5.1 Model (see `core/model.py`)

- `Episode(id, kind, agent, opened_at, closed_at, attributes)`
- `Claim(id, episode_id, type, made_at, attributes, origin, span)`
- `Evidence(record_type, external_ref, observed_at, attributes, occurred_at, version)`
- `CheckRun(check_id, check_version, kind, level, claim_id, result, reason, inputs_hash, derived, explanation)`
- `Verdict(claim_id, result, reasons, check_runs)`
- `Result`: `VERIFIED | CONTRADICTED | PENDING | UNVERIFIABLE`
- All datetimes are timezone-aware UTC. Naive datetimes are rejected.

### 5.2 Check definitions (YAML + CEL)

A simplified example (the real one is `packs/coding/checks/ci_result.yaml`):

```yaml
id: coding.tests_pass
version: 1.0.0
kind: state            # rule | state | temporal (judge and human are reserved)
level: L4
applies_to: [TESTS_PASS]
params:
  timeout_hours: {type: int, default: 6, min: 1, max: 48}
evidence:              # named, filtered evidence sets
  checks:
    record_type: check_result
    where: "e.ref == claim.attributes.ref"
derive:                # evaluated in order; later entries may use earlier ones
  test_checks: "checks.filter(c, is_test_check(c.name))"
  failed: "test_checks.exists(c, c.conclusion in ['failure', 'timed_out', 'cancelled'])"
outcomes:              # first match wins; the last must be `otherwise`
  - {when: "size(test_checks) == 0", result: UNVERIFIABLE, reason: NO_TEST_CHECKS}
  - {when: "failed", result: CONTRADICTED, reason: TEST_CHECK_FAILED}
  - {otherwise: true, result: VERIFIED, reason: ALL_TEST_CHECKS_PASSED}
explain: "{passed} of {total} test checks passed on {ref}"
```

Variables available to expressions: `claim`, `episode`, `params`, `now`, `stale` (a list of
evidence record types the source reports as stale), each named evidence set, and every
derived value. Each evidence item is exposed as a map with `record_type`, `external_ref`,
`observed_at`, `occurred_at` and its attributes.

**Missing values:** fields whose value is `None` are left out of maps, so expressions test for
them with `has(x.field)`. Do not compare with `null`; cel-python does not handle null
comparisons reliably.

Evaluation rules:

- Evidence is de-duplicated before evaluation.
- Inputs (check id and version, claim, episode, evidence used, params, `now`, `stale`) are
  hashed into `inputs_hash`.
- A check that raises an error during evaluation produces `UNVERIFIABLE` with reason
  `ENGINE_ERROR`. It never crashes the run and never produces `CONTRADICTED`.
- Expressions are compiled once and cached.

### 5.3 Extending CEL with pack functions

Packs can register pure, deterministic functions such as `is_test_check(name)`. Functions
are registered by name when the pack is loaded. A name collision between packs is an error.
Functions must not do I/O.

### 5.4 Aggregation (`core/aggregate.py`)

The verdict for a claim is decided by the first rule that matches:

1. Any `CONTRADICTED` result from a deterministic check (rule, state, temporal, human) gives
   `CONTRADICTED`.
2. Otherwise, any `PENDING` gives `PENDING`.
3. Otherwise, any `UNVERIFIABLE` gives `UNVERIFIABLE`.
4. Otherwise, if every check is `VERIFIED` (and there is at least one), the verdict is
   `VERIFIED`.
5. If no check applies, the verdict is `UNVERIFIABLE` with reason `NO_APPLICABLE_CHECKS`.

Judge checks are recorded but never change the verdict in v1.

### 5.5 Engine (`core/engine.py`)

```python
engine = Engine.from_packs([coding_pack])
verdicts = engine.verify(episode, claims, evidence, now=utc_now, stale=frozenset(), params={})
```

### 5.6 Packs (`core/pack.py`)

A pack is a directory containing:

- `pack.yaml` with the pack's id, version, claim types and check files;
- check YAML files;
- an optional Python module that registers CEL functions and provides the pack's extractor
  and detector.

## 6. GitHub source

- Uses the REST API only; a typical PR costs 4–10 requests (see docs/sources/github.md).
- Uses conditional requests (ETags) with a small on-disk cache in the CLI and benchmark.
- Authentication, in order of preference:
  1. `GITHUB_TOKEN` from the environment (used by the Action);
  2. the token from `gh auth token` if the GitHub CLI is logged in;
  3. no authentication (60 requests per hour, enough for a few PRs).
- On hitting a rate limit it stops cleanly. The evidence types it could not fetch are marked
  stale, and claims that need them become `UNVERIFIABLE` with reason `SOURCE_STALE`. The
  batch runner does not record such PRs; it retries them on the next run.
- It never checks out or runs PR code.

## 7. Error handling

| Where | Behaviour |
|---|---|
| Core check evaluation | `UNVERIFIABLE(ENGINE_ERROR)`; continue with other checks |
| Source (network, API) | Retries with backoff, then marks affected evidence types as stale |
| Action *(planned)* | Never fails the user's workflow because of our bugs. Posts a neutral check saying "could not verify" |
| CLI | Clear message and a non-zero exit code. `--json` gives machine-readable errors |

## 8. Testing

- **Unit tests** for every core module.
- **Non-coding fixture suite.** Customer-support refund claims with payment evidence. It must
  pass without any change to `core`.
- **Replay tests.** Identical inputs give an identical `inputs_hash` and result.
- **Golden sets** for the coding pack: a small seed set, and a real set of 402 labelled agent
  PRs from AIDev split into dev (gated) and test (reported). Target precision is ≥ 0.95. See
  `tests/packs/coding/golden/real/README.md` for the method and current results.
- **Fake GitHub API.** Tests run against an in-process fake; they never hit the network.
- **Lint and format** with ruff. Type hints throughout.

## 9. Open-source project hygiene

- Licence: Apache-2.0 (`LICENSE`), with third-party attributions in `NOTICE`.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, and issue and PR templates.
- Semantic versioning for the package, the Action (`v1` major tag) and each pack.
- Every change is a small, focused commit; `scripts/check.sh` must pass before each one.

## 10. Build plan

Each step is committed as its own commit, one file per commit where possible.

| Milestone | Status | Steps |
|---|---|---|
| M0 Core engine | done | model → hashing → evidence → expr → checks → aggregate → engine → pack loader → tests (unit, non-coding fixtures, replay) → CI workflow |
| M1 Coding pack | done | agent detection rules → claim grammar → referenced-commit resolution → checks (tests pass, build/lint, tests added, fixes issue, docs updated, deployed) → durability checks → golden set |
| M2 GitHub source + CLI | done | API client (auth chain, ETag cache, rate limits) → mappers → `second-source check <PR URL>` → Markdown/JSON reports → recorded fixtures |
| M3 Benchmark | in progress | AIDev sampling (dataset viewer API, see docs/benchmark.md) → batch runner with cache → statistics (Wilson intervals) → result files |
| M4 Action | built; release pending | `action.yml` → entrypoint (event parsing, wait-for-CI, check run + job summary) → daily durability mode → dispute command → one-click setup link |
| M5 Launch | planned | static site → dataset release → README, GIF and docs → open-source hygiene files → Marketplace listing |

## 11. Open decisions

- Docker vs composite Action (start with composite plus `uv` for speed; measure).
- Whether the benchmark site lives in this repo or a separate one.
