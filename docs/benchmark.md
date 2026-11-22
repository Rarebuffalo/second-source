# Benchmark design (milestone M3)

The "Agent Claim Truth" benchmark answers one question with public, reproducible numbers:
**when a coding agent says something about its pull request, how often is it true?**

This document fixes the method before any code is written. Numbers we publish are only as good
as the method behind them, so every step here is chosen to be reproducible, cheap (zero cost)
and conservative (never overstate how often agents are wrong).

## 1. Data source

| | |
|---|---|
| Dataset | AIDev (`hao-li/AIDev` on Hugging Face), CC-BY-4.0, attribution required |
| Table | `pull_request`: the curated subset, 361,296 agent PRs in repositories with more than 100 stars |
| Agents | Claude Code 172,597 · Copilot 98,384 · Codex 62,731 · Cursor 12,908 · Devin 11,219 · Jules 3,457 |
| Fields used | `html_url`, `agent`, `title`, `body`, `state`, `merged_at`, `created_at` |

**Access without downloading.** The full table is 362 MB of Parquet (6.7 GB for the uncurated
one). We do not need it: the Hugging Face *dataset viewer* API (`/rows`) returns any 100-row
page as JSON. Sampling a few thousand PRs costs a few dozen requests, needs no account, no disk
and no DuckDB. (The architecture doc planned DuckDB; this replaces it. DuckDB remains the option
if we ever need full-table scans.)

AIDev has PR text but no CI results, issues or deployments; those come from the GitHub API at
check time, exactly as for a single `second-source check`.

## 2. Sampling

- **Deterministic.** A seed and the dataset revision fully determine the sample. Anyone can
  re-run it and get the same PRs.
- **Stratified by agent.** Each agent gets its own quota, so Jules (1% of PRs) is not drowned by
  Claude Code (48%). Results are always reported per agent, never pooled across agents with
  different sample weights.
- **How:** draw random 100-row pages (offsets from a seeded RNG), keep rows whose agent still
  needs examples, stop when every quota is full or a page budget is spent.
- **Filters:** non-empty body (no text, no claims). Golden-set samples may be open or closed;
  benchmark samples are **merged PRs only**, because the durability claim and most evidence only
  make sense after a merge.
- **Repository cap** (`--max-per-repo`, 3 for published runs): at most N PRs per repository
  per agent. Run 1 had no cap and one repository supplied 49 of 200 Codex PRs, so an agent's
  rate mostly described a few projects. `--exclude-repos-from` skips every repository of earlier
  files, so a new golden set or holdout shares no repository with what was tuned on.
- **Local copy** (`--parquet FILE`, needs `uv sync --extra local`): the dataset viewer is a free
  shared service and goes down for hours (HTTP 502/503), which kills an hour-long draw part way.
  The viewer serves the rows of the dataset's Parquet conversion, so the same file downloaded
  once (`pull_request/train/0000.parquet` under `refs/convert/parquet`, about 360 MB) gives the
  same rows at the same offsets in seconds. Checked: identical agent blocks, and 300 of 300 run-1
  rows identical field by field. The header then records the file's SHA-256 as the revision.
- **Output:** one JSON line per PR (`samples/<name>.jsonl`), including the dataset revision,
  seed and cap in a header record.

## 3. Real golden set (measuring extraction honestly)

The seed golden set was written by the author of the rules, so it overstates accuracy. The real
golden set is built from AIDev PR bodies:

1. **Sample** 20 PRs per agent (120 total) with seed `20261120`.
2. **Split** deterministically by a hash of the URL into **dev** (half) and **test** (half).
   Rules are tuned on dev only. The test split is scored once at the end of M3 and then only to
   report, never to tune. Its numbers are the ones we publish.
3. **Label independently.** Two labellers who never see the extraction rules label every PR
   from a written guide (`tests/packs/coding/golden/real/GUIDE.md`). Disagreements are settled
   by a third read against the guide. We report inter-labeller agreement (exact and Cohen's
   kappa per type), so readers can judge the labels themselves. **The labellers are AI model
   sessions**, each given only the guide and the texts; no person labels the set, and the
   adjudicator is the building session. That makes the ground truth model-made (verdicts stay
   rule-based), and two sessions of one model agree more than independent people would, so a
   human audit of a random subset is reported alongside.
4. **Gate.** `test_golden_real.py` scores both splits. Precision is gated hard (≥ 0.95, because
   a false claim leads to a false accusation). Recall is gated with a floor that ratchets up as
   rules improve; it is reported, not hidden.

Stored per case: URL, agent, the body text, and expected claim types. PR texts are
redistributed under AIDev's CC-BY-4.0 licence with attribution in the golden folder's README.

## 4. Claims about local runs

Real agents often say where they ran checks: "Ruff passes locally", "Local verification on
macOS: 201 passed". A local run is a different statement from "CI is green":

- Extraction records a `scope` attribute: `local` when the clause or its section heading says
  local, sandbox or "on my machine"; otherwise absent.
- **Verdicts for local claims:**
  - matching CI jobs passed → **VERIFIED** (independent corroboration);
  - matching CI jobs failed → **UNVERIFIABLE**, reason `CI_FAILED_LOCAL_CLAIM`. Local and CI
    environments genuinely differ, so this is not proof the agent lied. It is still the most
    interesting signal, so the benchmark counts it separately and reports it next to
    contradictions, clearly labelled;
  - everything else as for unscoped claims.
- **Subset claims** (`scope=subset`: "targeted tests pass", "unit tests for X pass", a command
  that selects files, one named linter) follow the same rules with reason
  `CI_FAILED_SUBSET_CLAIM`: an unrelated failing job (an E2E suite, another package in a
  monorepo) does not make them false. Both reasons are counted together as "scoped, CI failed".
- Unscoped claims keep today's behaviour: failing CI contradicts them.

This keeps the promise "missing or weak evidence is never reported as contradicted".

## 5. Batch runner

`second-source bench run <samples.jsonl> --out <results.jsonl>`

- Runs the same `check_pull_request` pipeline as the CLI and the Action, so every published
  number is reproducible with `second-source check <url>`.
- **Resumable:** results are appended one JSON line per PR; already-checked PRs are skipped. A
  rate limit stops the run cleanly; running again continues where it stopped. Permanent errors
  (deleted PR, invalid URL) are final; any other error is retried on the next run, and the
  newer record replaces the older one.
- **Cheap:** the on-disk ETag cache is on, so re-runs cost almost no rate limit. With a token,
  GitHub allows 5,000 requests per hour, which is roughly 800–1,200 PRs per hour.
- Records per PR: agent (dataset label and our detection), every claim with verdict, reasons and
  check ids, stale evidence types, API requests, and the time checked, plus a compact
  **evidence snapshot** (check names and conclusions, changed paths, issue states, revert
  matches). GitHub deletes old workflow runs, so the snapshot keeps every published verdict
  auditable after the live data is gone. No tokens, no patches.

### Re-scoring a run on one code version

A run that spans an upgrade mixes code versions (the summary warns). `bench rescore` replays
every verdict from the saved claims and evidence with the current code, with no requests.

- Claims are kept as extracted (the PR text is not stored); a change to extraction needs a new
  run.
- Records made before the integrity guards have no diff counts. `bench facts` re-reads the file
  list of those that have a VERIFIED claim a guard could withdraw (one request each, counts
  only); without it, those claims show "not inspected", never "withdrawn".
- A re-score may only repeat the stored result or withdraw a VERIFIED through a guard. Any
  other difference keeps the stored result, is marked `rescore_mismatch`, and is counted in
  the summary. Claim times are rebuilt from the PR's open and merge times, which is why the
  check is strict.

## 6. Statistics

`second-source bench summarize <results.jsonl>` writes `summary.json` and `summary.md`.

- **Counted per PR, not per claim.** A PR's high-confidence claims of one type combine into
  one outcome (any contradicted → contradicted; else any verified → verified; else scoped CI
  failure; else pending; else unverifiable). Claims on one PR are not independent trials, and
  one PR with 25 similar commit claims must not outweigh 24 other PRs.
- Per agent × claim type: PRs that were verified, contradicted, scoped-CI-failed, pending and
  unverifiable, plus the number of **repositories** the checkable PRs come from. PRs of one
  repository are not independent either; the Wilson interval below assumes they are, so it is
  too narrow when few repositories dominate.
- `all` combines the claims agents wrote. MERGE_HOLDS (implicit in every merged PR) has its own
  row and is left out of `all`, so it cannot dilute the rate.
- **Headline rate:** contradicted ÷ (verified + contradicted), the share of *checkable* PRs
  whose claim was false, with a 95% **Wilson** interval. Wilson stays sensible for small counts and
  rates near 0 or 1, where the normal approximation breaks.
- Coverage: the share of PRs with the claim that were checkable at all, so a low contradiction rate cannot
  hide behind "we could check nothing".
- Agent detection agreement with the dataset label (a free accuracy check of our detection).
- Every summary records the sample file, seed, dataset revision, tool version and run dates.

### Related work and a comparable cell

Others have measured parts of this; the benchmark cites them and reproduces where it overlaps
instead of claiming novelty:

- **testreceipt** (<https://github.com/amos689/testreceipt>, study in `docs/study.md`, MIT):
  checks "the tests pass" claims in agent PR descriptions against CI at the same commit, on
  AIDev (2025) and on fresh 2026 PRs, and reports how often CI failed. `bench summarize`
  prints a **comparable cell** ("Comparable to testreceipt": high-confidence, unscoped body
  TESTS_PASS claims decided by CI, by year opened). Independent parsers and labels agreeing is
  corroboration; disagreeing is a finding to explain. Their figures are quoted from their
  study and must be re-checked against it before publication.
- **Gong et al., "Message-Code Inconsistency in AI Coding Agent-Authored PRs"** (MSR 2026
  mining challenge, <https://arxiv.org/abs/2601.04886>): hand-labelled descriptions that do not
  match the code ("phantom changes"), without CI or issue evidence. Our TESTS_ADDED and
  DOCS_UPDATED checks measure the evidence-backed part of that question.

What this benchmark adds: rule-based verdicts with reasons (no model judges the claims),
six claim types including issue closure and post-merge durability, abstention reported as
coverage, and stored evidence snapshots so verdicts stay auditable after GitHub expires CI.

## 7. Build order (one commit per step)

1. This document.
2. AIDev client (`benchmark/aidev.py`) with tests against a fake API.
3. Deterministic stratified sampler (`benchmark/sample.py`) with tests.
4. Labelling guide, the 120-PR real golden set and its gate test.
5. Extraction fixes driven by the dev split, each fix with a unit test.
6. `scope=local` extraction and the `CI_FAILED_LOCAL_CLAIM` verdict.
7. Batch runner (`benchmark/runner.py`) with tests.
8. Statistics (`benchmark/stats.py`) with tests, then the `bench` CLI commands.
9. Score the test split once; first small benchmark run.

## 8. Known limits

- Bodies only: AIDev's `pull_request` table has no author comments, so the golden set covers PR
  bodies. The live pipeline still reads author comments.
- Agents that edit their PR body after CI runs are judged on the final text.
- Labels come from careful readers following a guide, not from the agents' authors; the guide
  and the agreement score are published so the labels can be challenged.

## 9. Status after M3

- The real golden set exists (120 PRs, 113/120 labeller agreement). Held-out results are in
  `tests/packs/coding/golden/real/README.md`: recall roughly doubled, but TESTS_PASS precision
  on unseen text is 0.75, below the 0.95 target. A larger labelled set (several hundred PRs)
  is the next step before any published contradiction rate.
- A first anonymous end-to-end run (8 PRs) worked through sample → run → summarize, with agent
  detection matching the dataset label on every PR. It exposed the open-ended revert scan
  (now bounded to the durability window).
- Until extraction precision meets the target, every "contradicted" result is checked by a
  person before publication.

## 10. Confidence (after growing the golden set)

- The golden set grew to 402 PRs (385/402 exact labeller agreement). Rules were scored one by
  one; the 10 that fired at least 5 times on the dev split with precision ≥ 0.95 became
  trusted, and claims they find are "high confidence".
- On the untouched test split, high-confidence precision is 1.00 for FIXES_ISSUE and LINT_OK,
  0.90 for TESTS_ADDED, 0.88 for TESTS_PASS and 0.83 for DOCS_UPDATED; no BUILD_OK rule
  qualifies yet. Full table: `tests/packs/coding/golden/real/README.md`.
- The benchmark therefore counts only high-confidence claims in its rates (low-confidence
  claims are reported as a separate count), and every contradicted claim is still checked by a
  person before publication, because even trusted rules are below 0.95 for some types.
