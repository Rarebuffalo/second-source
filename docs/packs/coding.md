# Coding pack design

The coding pack teaches the domain-neutral engine about coding agents working through pull
requests (PRs). It lives in `src/second_source/packs/coding/`.

## 1. Mapping to core concepts

| Core concept | Coding meaning |
|---|---|
| Episode | One pull request opened by a coding agent |
| Claim | A statement in the PR title, body, a commit message or an agent comment, e.g. "All tests pass" |
| Evidence | CI check results, changed files, issue state, PR merge state, reverts, deployments |
| Verdict | Whether each claim holds |

## 2. Claim types

| Claim type | Example text | Key attributes | Checks |
|---|---|---|---|
| `TESTS_PASS` | "All tests pass", "- [x] Tests pass", "CI is green" | `ref` | `coding.tests_pass` |
| `BUILD_OK` | "Build succeeds", "compiles cleanly" | `ref` | `coding.build_ok` |
| `LINT_OK` | "Lint passes", "no lint errors" | `ref` | `coding.lint_ok` |
| `TESTS_ADDED` | "Added unit tests for the parser" | (none) | `coding.tests_added` |
| `FIXES_ISSUE` | "Fixes #123", "Closes owner/repo#45" | `issue`, `repo` | `coding.fixes_issue`, `coding.fixes_issue_durable` |
| `DOCS_UPDATED` | "Updated the README" | (none) | `coding.docs_updated` |
| `DEPLOYED` | "Deployed to staging" | `ref`, `environment` | `coding.deployed` |
| `MERGE_HOLDS` | Implicit: created for every merged agent PR | `merge_sha` | `coding.not_reverted` |

`ref` is the commit the claim refers to: the PR head commit at the moment the text was
written. The source supplies it (see section 4), and extraction copies it onto the claim.

`MERGE_HOLDS` is an implicit claim: merging a PR asserts that the change is good. Making it
a claim lets the L5 "stayed true" checks use the same engine as everything else.

## 3. Extraction rules

Extraction is rule-based. v1 uses no AI model.

1. Remove the parts of the text that are not the agent's own statements:
   - fenced code blocks;
   - inline code;
   - quoted lines (`>`);
   - HTML comments.
2. Split the text into clauses, using lines, sentences and checklist items.
3. Skip clauses that are not assertions:
   - unchecked checklist items (`- [ ]`);
   - questions;
   - plans and hedges: "will", "should", "need to", "TODO", "not yet", "once";
   - negations: "not", "n't", "fail", "failing".
4. Match each remaining clause against the per-type patterns. A clause can yield claims of
   several types.
5. Issue references follow GitHub's closing keywords (close, fix and resolve, in any tense)
   followed by `#N` or `owner/repo#N`. Each issue becomes its own claim.
6. Claim ids are deterministic: a hash of episode id, origin, type, span and position. The
   same PR always gives the same ids.

Precision matters more than recall. When unsure, do not create a claim. The golden set in
`tests/packs/coding/golden/` measures both, per claim type, with targets of precision ≥ 95%
and recall ≥ 90%.

## 4. Evidence contract (what a source must provide)

**Episode attributes.** A source builds one `Episode` per PR with `kind="pull_request"`, the
detected agent, `opened_at`, `closed_at`, and these attributes:

- `repo`: `owner/name`;
- `number`;
- `url`;
- `head_branch`;
- `base_branch`.

**Missing values.** Attributes with no value are omitted, never set to null. Checks test them
with `has()`. Environment names in `deployment_status` are lower-cased by the source.


Every record is an `Evidence` with the `record_type`, `external_ref` and attributes below.
Timestamps are timezone-aware UTC.

| record_type | external_ref | Attributes | Notes |
|---|---|---|---|
| `check_result` | check id (or `context@sha` for statuses) | `ref`, `name`, `status` (`queued` / `in_progress` / `completed`), `conclusion` (`success` / `failure` / `neutral` / `cancelled` / `timed_out` / `action_required` / `skipped` / `stale` / null), `source` (`check_run` / `status`) | `version` = attempt number, so de-duplication keeps the latest re-run |
| `changed_file` | path | `path`, `status` (`added` / `modified` / `removed` / `renamed`), `additions`, `deletions` | No patches or code are ever stored |
| `issue_state` | `repo#number` | `repo`, `number`, `state` (`open` / `closed`), `closed_at`, `last_reopened_at` | |
| `pr_state` | PR number | `state` (`open` / `closed`), `merged`, `merged_at`, `merge_sha` | One record per episode |
| `revert` | reverting commit sha | `reverted_sha`, `at` | Found by scanning the default branch after merge |
| `deployment_status` | deployment id | `ref`, `environment`, `state` (`success` / `failure` / `error` / `in_progress` / `queued` / `pending` / `inactive`), `at` | |

Sources report record types they could not refresh in `stale`. Checks then return
`UNVERIFIABLE` with reason `SOURCE_STALE`.

## 5. Agent detection

Detection runs as data-driven rules over author login, author type, head branch name, PR
body markers and commit trailers. Each rule yields an agent name and the signal used.

| Agent | Signals (to be verified against the AIDev dataset's `agent` labels) |
|---|---|
| GitHub Copilot | author login `Copilot`; branch prefix `copilot/` |
| OpenAI Codex | branch prefix `codex/` |
| Claude Code | body marker "Generated with Claude Code"; trailer `Co-Authored-By: Claude` |
| Cursor | branch prefix `cursor/`; author `cursor[bot]` / `cursoragent` |
| Devin | author `devin-ai-integration[bot]` |
| Jules | author `google-labs-jules[bot]` |

Rules are versioned with the pack. The benchmark measures detection precision against the
dataset labels (target ≥ 98%).

## 6. Classifying checks and files

Helper functions that checks can call:

| Function | Purpose |
|---|---|
| `is_test_check(name)` | Matches CI job names for test runs: test, pytest, jest, vitest, mocha, spec, unit, integration, e2e, rspec, junit, … |
| `is_build_check(name)` | Matches build, compile and bundle jobs |
| `is_lint_check(name)` | Matches lint, eslint, ruff, flake8, pylint, clippy, golangci, prettier, format and typecheck jobs |
| `is_test_path(path)` | Matches test files by common conventions across languages |
| `is_doc_path(path)` | Matches documentation files (`docs/`, README, CHANGELOG, `.md`, `.rst`, `.mdx`) |

A name that cannot be classified does not count. If no test check can be identified, the
claim is `UNVERIFIABLE` rather than guessed.

## 7. Not in v1

- The heuristics for follow-up fixes and broken default-branch CI after merge are planned,
  but not in this version.
- Model-assisted extraction is not included.
- Per-repository configuration of patterns (for example a custom test job name) comes later,
  through an optional config file.
