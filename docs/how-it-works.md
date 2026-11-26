# How it works

Second Source turns a pull request into verdicts in five steps. Every step is deterministic, so
the same evidence always gives the same result.

```text
pull request ─> detect agent ─> extract claims ─> fetch the evidence those claims need
             ─> run checks (YAML + CEL) ─> verdicts ─> check run, CLI report or JSON
```

## 1. Detect the agent

Second Source reads the pull request's author, branch name, body and commit messages, and looks
for each agent's signature: a `codex/` branch, Copilot's bot account, a "Generated with Claude
Code" line, a `Co-Authored-By` trailer and so on. In the GitHub Action, pull requests with no
detected agent are skipped by default.

## 2. Extract claims

Rules find statements of fact in the agent's own words: "all tests pass", "Fixes #12",
"deployed to staging". Text that is not the agent's statement is removed first: code blocks,
quotes, HTML comments, unchecked to-do items and review bots' sections. A clause that is
negated or hedged ("tests should pass", "19/22 tests passing") is not a claim.

Each claim records the commit it refers to. A commit message refers to its own commit. A PR body
can be edited until the last push, so its claims carry both the commit when the PR was opened
and the final head; a claim is verified on the final head and contradicted only if it fails on
both.

The rules are measured one by one on a labelled set of real agent pull requests; claims found by
rules with precision of at least 0.95 are high confidence. See [Accuracy](accuracy.md).

## 3. Fetch evidence

Only the evidence the claims need is fetched from the GitHub API: check runs and commit statuses
for the claim's commits, workflow runs (to know whether CI is still coming), the changed files,
the referenced issues, deployments, and the default branch's commits after the merge, to look
for reverts. The Action also reads the head commit's CI and the file list on every pull
request, for the evidence summary.

Diffs are read once, to count facts such as added skip markers or removed test cases, and then
dropped: no code is stored.

## 4. Run checks

Each check is a YAML file with [CEL](https://cel.dev) expressions. A check selects the evidence
it needs, derives values, and picks the first outcome whose condition holds. For example, the CI
check verifies a "tests pass" claim when every matching test job passed, waits while CI is
running, and contradicts it only when a matching job failed on the claim's commit (abridged):

```yaml
outcomes:
  - {when: "'check_result' in stale", result: UNVERIFIABLE, reason: SOURCE_STALE}
  - {when: "failed_somewhere && local", result: UNVERIFIABLE, reason: CI_FAILED_LOCAL_CLAIM}
  - {when: "failed_everywhere", result: CONTRADICTED, reason: CI_JOB_FAILED}
  - {when: "awaiting", result: PENDING, reason: AWAITING_CI}
  - {when: "size(ci_records) == 0", result: UNVERIFIABLE, reason: NO_CI_RECORDS}
  - {otherwise: true, result: VERIFIED, reason: ALL_MATCHING_CI_JOBS_PASSED}
```

(The full check is [ci_result.yaml](../src/second_source/packs/coding/checks/ci_result.yaml).)

Guard checks protect against a false verification: they can only withdraw a "verified" or add a
caution, never contradict. Every check run records a hash of its exact inputs, so a verdict can
be replayed.

## 5. Combine into a verdict

A claim's checks are combined into one verdict with its reasons. Evidence that could not be
read, or that is missing or ambiguous, makes a claim unverifiable; it never makes it
contradicted. The coding pack keeps the complete list of ways a claim can be contradicted in
[packs/coding.md](packs/coding.md), and a test ties that list to the check files.

## Over time

Some claims can only be settled later. After a merge, "Fixes #12" is watched for 30 days for a
reopened issue, and every merge is watched for a revert. The GitHub Action re-checks recent agent
pull requests daily, and updates the check run in place.

## Domain-neutral core

The engine knows nothing about code. Claims, evidence and checks are generic; the coding pack
supplies the claim types, extraction rules and checks, and the GitHub source supplies the
evidence. A customer-support fixture pack in the tests keeps the core honest. The
[architecture](ARCHITECTURE.md) describes the layers and contracts.
