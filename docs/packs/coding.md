# Coding pack

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
| `TESTS_PASS` | "All tests pass", "- [x] Tests pass", "CI is green" | `ref`, `scope` | `coding.ci_result` |
| `BUILD_OK` | "Build succeeds", "compiles cleanly" | `ref`, `scope` | `coding.ci_result` |
| `LINT_OK` | "Lint passes", "no lint errors" | `ref`, `scope` | `coding.ci_result` |
| `TESTS_ADDED` | "Added unit tests for the parser" | (none) | `coding.tests_added` |
| `FIXES_ISSUE` | "Fixes #123", "Closes owner/repo#45" | `issue`, `repo` | `coding.fixes_issue`, `coding.fixes_issue_durable` |
| `DOCS_UPDATED` | "Updated the README" | (none) | `coding.docs_updated` |
| `DEPLOYED` | "Deployed to staging" | `ref`, `environment` | `coding.deployed` |
| `MERGE_HOLDS` | Implicit: created for every merged agent PR | `merge_sha` | `coding.not_reverted` |

`ref` is the commit the claim refers to: the PR head commit at the moment the text was
written. The source supplies it (see section 4), and extraction copies it onto the claim.

`final_ref` is set on test, build and lint claims from the PR body or title when the PR gained
commits after it opened. Those texts can be edited (Copilot rewrites its body as it works, from
an empty "Initial plan" commit), so either commit may be meant. CI must pass on the final head
to verify the claim and fail on both commits to contradict it; a failure on only one is
`UNVERIFIABLE` with reason `CLAIM_COMMIT_AMBIGUOUS`.

`scope` is `local` when the text says a test, build or lint check ran locally ("tests pass
locally", or a claim under a "## Local verification" heading). Passing CI still verifies a
local claim, but failing CI makes it `UNVERIFIABLE` with reason `CI_FAILED_LOCAL_CLAIM`
instead of `CONTRADICTED`: local and CI environments differ, so it is a signal, not proof.
`scope` is `subset` when the claim covers only part of the checks: "targeted tests pass",
"unit tests for X pass", "the 12 new tests pass", a command that selects files or tests
(✅ `pytest tests/test_x.py`), or one named linter (✅ `biome lint`). Failing CI gives
`UNVERIFIABLE` with reason `CI_FAILED_SUBSET_CLAIM`, because the failing job may be one the
claim never covered. Claims that do neither are read as claims about the change itself, and
failing CI contradicts them. (See docs/benchmark.md, section 4.)

**Absence is not proof.** `TESTS_ADDED` and `DOCS_UPDATED` are never `CONTRADICTED`. When the
PR changes no test (or documentation) file, the result is `UNVERIFIABLE` with reason
`NO_TEST_FILES_FOUND` (`NO_DOC_FILES_FOUND`), shown in words. Tests often live inside source
files (Rust `#[cfg(test)]`, in-source vitest, Racket `module+ test`) and documentation in code
(docstrings, XML doc comments, roxygen), and a sentence that only looks like a claim ("creates
test directories", "doc cache") rarely comes with matching files. In the run-2 audit only 8 of
36 such contradictions were really false claims. Likewise, an issue reopened after the merge
contradicts `FIXES_ISSUE` only while it is still open; reopened and closed again is
`UNVERIFIABLE` (`REOPENED_THEN_CLOSED`): maintainers reopen to verify or to wait for a release,
and a later PR may have fixed it.

`CONTRADICTED` comes only from these, each with its exceptions:

| Claim | Contradicted when | Reason | Not when (UNVERIFIABLE or PENDING instead) |
|---|---|---|---|
| Tests pass, build, lint | a matching CI job failed on the claim's commit (and on the final head, if the PR gained commits) | `coding.ci_result: CI_JOB_FAILED` | local or subset claims; timed-out or cancelled jobs; failure on only one of two commits |
| Fixes an issue | the issue is still open after a merge into the default branch, for a closing keyword in the body or a commit message, same repository | `coding.fixes_issue: ISSUE_STILL_OPEN`, `coding.fixes_issue_durable: ISSUE_OPEN` | moments after the merge (`CLOSE_PENDING`); title, comment or cross-repository references (`NOT_AUTO_CLOSED`); merges into another branch |
| Fixes an issue | the issue was reopened after the merge and is still open, for a closing keyword in the body or a commit message, same repository | `coding.fixes_issue_durable: ISSUE_REOPENED` | reopened then closed again (`REOPENED_THEN_CLOSED`); title, comment or cross-repository references (`NOT_AUTO_CLOSED`) |
| Deployed | a deployment of the commit to that environment failed, nothing is still deploying, and the source saw every deployment of the commit | `coding.deployed: DEPLOYMENT_FAILED` | a retry in progress; more deployments than the source lists |
| Merge holds | a revert of the merge commit on the base branch | `coding.not_reverted: REVERTED` | merges into another branch; the revert itself reverted (`REVERT_UNDONE`) |

This table is the complete list: `tests/packs/coding/test_contradiction_paths.py` fails if a
check gains or loses a `CONTRADICTED` outcome without the table changing too.

`MERGE_HOLDS` is an implicit claim: merging a PR asserts that the change is good. Making it
a claim lets the L5 "stayed true" checks use the same engine as everything else.

**Merges into a non-default branch.** GitHub closes linked issues only for merges into the
default branch, and a feature branch may be deleted later, so `FIXES_ISSUE` (issue still open)
and `MERGE_HOLDS` (revert scan) give `UNVERIFIABLE` with reason `NOT_DEFAULT_BRANCH` for those
PRs. Known cost: a repository that renamed its default branch (`master` → `main`) keeps the old
name on old PRs, so their claims become `NOT_DEFAULT_BRANCH` too. That is safe (never a false
contradiction) but lowers coverage for such repositories.

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

7. Every rule has a stable name (for example `tests_pass.plain`, `command.marked`,
   `fixes_issue.closing_ref`), recorded on the claim as `rule`. Rules whose precision held the
   target on the golden set are listed in `packs/coding/trusted_rules.yaml`; their claims get
   `confidence: high`, all others `confidence: low`. Precision was measured on PR bodies only,
   so claims from titles, commit messages and comments are always `low` (unless the body says
   the same thing). Reports label low-confidence claims, and
   the benchmark leaves them out of its rates. `second-source bench rules` prints per-rule
   precision, and `--select` regenerates the list.

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
with `has()`. Repository and environment names may use any case: checks compare repositories
case-insensitively (`same_repo`) and normalise environment aliases on both sides
(`env_matches`, e.g. `prod` = `production`).


Every record is an `Evidence` with the `record_type`, `external_ref` and attributes below.
Timestamps are timezone-aware UTC.

| record_type | external_ref | Attributes | Notes |
|---|---|---|---|
| `check_result` | `<ref>/<job name>` (one key per job per commit) | `ref`, `name`, `status` (`queued` / `in_progress` / `completed`), `conclusion` (`success` / `failure` / `neutral` / `cancelled` / `timed_out` / `action_required` / `skipped` / `stale`; omitted while running), `source` (`check_run` / `status`) | `version` = the run's numeric id (or a timestamp for statuses). A GitHub re-run gets a new, larger id, so de-duplication keeps only the newest run of each job |
| `ci_workflow_run` | `<ref>/workflow:<name>` | `ref`, `name`, `status` (`queued` / `in_progress` / `waiting` / `completed` …), `conclusion` (`action_required` when held for approval), `event` | Only tells whether CI on a commit is still coming (queued, waiting on `needs:` jobs, held for approval); never decides a verdict. `version` = run id |
| `changed_file` | path | `path`, `status` (`added` / `modified` / `removed` / `renamed`), `additions`, `deletions`, plus integrity counts from the diff: `patch_available`, `test_skips_added`/`_removed`, `test_cases_added`/`_removed`, `asserts_added`/`_removed`, `test_cmd_weakened`, `coverage_gate_lowered`, `lint_suppressions_added`, `nonblank_added`, `marker_lines` | The patch is read once to count (`packs/coding/diff_facts.py`), then dropped: no patches or code are ever stored |
| `changed_files_summary` | PR number | the PR's totals of the counts above, plus `removed_test_files`, `test_source_changed`, `code_net`, `code_removed`, `ci_config_changed`, `ci_config_files`, `lint_config_changed`, `uninspected` | One per PR, because CEL cannot sum. Same file list, no extra request |
| `issue_state` | `repo#number` | `repo`, `number`, `state` (`open` / `closed`), `closed_at` (required when closed), `last_reopened_at` | `closed_at` lets checks reject issues that were closed before the PR merged |
| `pr_state` | PR number | `state` (`open` / `closed`), `merged`, `merged_at`, `merge_sha` | One record per episode |
| `revert` | reverting commit sha | `reverted_sha`, `at` | Found among default-branch commits that touch one of the PR's files in the 30 days after merge: "This reverts commit <sha>", "Reverts owner/repo#N", or `Revert "<PR title>"` |
| `deployment_status` | deployment id | `ref`, `environment`, `state` (`success` / `failure` / `error` / `in_progress` / `queued` / `pending` / `inactive`), `at` | |

Sources report record types they could not refresh in `stale`. Checks then return
`UNVERIFIABLE` with reason `SOURCE_STALE`.

**Command status lines.** A line such as `- ✅ pytest -q` counts as a claim only if the inline
code starts with a known test, build or lint runner (pytest, npm, cargo, ruff, ...). File
paths and identifiers in inline code never produce claims.

### Integrity guards (protecting users from a false VERIFIED)

The cardinal rule protects agents from false accusations. Guards protect reviewers from a
false green tick: CI passing proves little if the same PR shaped its own evidence (on
`pull_request` events GitHub runs the PR's own copy of the workflow). Guards (`role: guard`,
see docs/ARCHITECTURE.md §5.4) can only withdraw a VERIFIED or add a caution; they can never
produce CONTRADICTED.

| Guard | Kind | Claims | Reason when it fires |
|---|---|---|---|
| `coding.integrity_guard` | withdraws (required) | TESTS_PASS | `PR_SKIPS_TESTS` (an unconditional skip or `.only` added in a test file), `PR_WEAKENS_TEST_COMMAND` (`\|\| true`, `--deselect`, `-k not`, `--ignore` … on the test command itself; `continue-on-error` on the test step itself; `-DskipTests` / `-x test` added to an existing test, verify or check command), `PR_LOWERS_COVERAGE_GATE` (a weaker number for a real coverage key, or a gate deleted in the PR and added nowhere) |
| `coding.ci_weakening_caution` | caution | TESTS_PASS | `CI_TOLERATES_TEST_FAILURES` (`continue-on-error` at job level on a job that runs tests), `BUILD_SKIPS_TESTS` (`-DskipTests` / `-x test` added to an existing build, package or deploy command) |
| `coding.tests_added_guard` | withdraws (required) | TESTS_ADDED | `ADDED_TESTS_ARE_SKIPPED` |
| `coding.ci_config_caution` | caution | TESTS_PASS, BUILD_OK, LINT_OK | `PR_CHANGES_CI_CONFIG` (workflow or test config files) |
| `coding.tests_removed_caution` | caution | TESTS_PASS | `PR_REMOVES_TESTS` (≥ 2 more test cases removed than added, code kept) |
| `coding.lint_caution` | caution | LINT_OK | `LINT_SUPPRESSIONS_ADDED`, `LINT_CONFIG_CHANGED` |
| `coding.matching_jobs_caution` | caution | TESTS_PASS, BUILD_OK, LINT_OK | `MATCHING_JOB_SKIPPED` |
| `coding.integrity_inspection` | caution | TESTS_PASS, TESTS_ADDED | `NOT_INSPECTED` (no file list, or no diff for a test or CI file) |

A `NO_TEST_CASE_ADDED` caution (test files changed, no new test function) was retired after the
run-2 audit found it right in 1 of 6 cases: table-driven rows, `rstest` `#[case]`s and
fixture-driven cases add tests without a new test function.

Withdrawing signals are deliberately narrow (declaration-level skips outside comments, weakening
on the test command itself or its step, a weaker number for a real coverage key in its own
file); their precision on real PRs is being measured on benchmark run 2 and will be reported
here. Cautions are real but too common (CI config
changes in about 6–17% of agent PRs) or not yet measured. A caution is promoted to a
withdrawal only after audited precision of at least 0.90 on at least 30 flagged PRs. Runtime
skips (`pytest.skip(`, `t.Skip(`) are not counted: most are legitimate environment guards.
If the file list cannot be read, nothing is withdrawn and `NOT_INSPECTED` is shown instead.

**Known gaps** (not detected yet): a test job disabled with `if: false`, a deleted test step,
`set +e` before tests, `testPathIgnorePatterns` / `collect_ignore`, TestNG `enabled = false`,
RSpec `skip:` / `pending`, NUnit `[Explicit]`, Go runtime skips, and Rust `#[ignore]` on inline
tests in source files. Precision of each withdrawing signal on real PRs is not yet published;
it comes from the audited benchmark run 2.

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
| `job_matches(claim_type, name)` | Picks the CI jobs that verify a claim type (test jobs for `TESTS_PASS`, build jobs for `BUILD_OK`, lint jobs for `LINT_OK`) |
| `same_repo(a, b)` | Case-insensitive repository comparison |
| `env_matches(evidence, claimed)` | Environment match after alias normalisation on both sides |
| `is_test_check(name)` | Matches CI job names for test runs: test, pytest, jest, vitest, mocha, spec, unit, integration, e2e, rspec, junit, … |
| `is_build_check(name)` | Matches build, compile and bundle jobs |
| `is_lint_check(name)` | Matches lint, eslint, ruff, flake8, pylint, clippy, golangci, prettier, format and typecheck jobs |
| `is_test_path(path)` | Matches test files by common conventions across languages |
| `is_doc_path(path)` | Matches documentation files (`docs/`, README, CHANGELOG, `.md`, `.rst`, `.mdx`, `man/*.Rd`) |

A name that cannot be classified does not count. If no test check can be identified, the
claim is `UNVERIFIABLE` rather than guessed.

## 7. Not in v1

- The heuristics for follow-up fixes and broken default-branch CI after merge are planned,
  but not in this version.
- Model-assisted extraction is not included.
- Per-repository configuration of patterns (for example a custom test job name) comes later,
  through an optional config file.
