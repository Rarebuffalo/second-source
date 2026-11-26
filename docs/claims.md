# Claims and verdicts

This page explains what Second Source looks for in a pull request, the four verdicts, and every
reason a report can give. The coding pack's design is in [packs/coding.md](packs/coding.md).

## Where claims come from

Second Source reads the texts an agent writes on a pull request: the title, the body, commit
messages and the author's own comments. It ignores code blocks, quoted text, HTML comments,
unchecked to-do items and sections written by review bots. Rules find the claims; no language
model is involved.

Each claim is marked high or low confidence. High-confidence claims were found by a rule whose
precision is at least 0.95 on the labelled development set (see [Accuracy](accuracy.md)); only
they decide a check's conclusion. Low-confidence claims are shown, marked as such.

## Claim types

| Claim | Example wording | Verified when | Contradicted when |
|---|---|---|---|
| Tests pass (`TESTS_PASS`) | "All tests pass", "✅ `pytest`" | Every CI test job on the claim's commit passed | A test job failed on that commit (and on the final head, if the PR gained commits) |
| Build succeeds (`BUILD_OK`) | "Build succeeds" | Every CI build job passed | A build job failed |
| Lint passes (`LINT_OK`) | "Lint passes", "no ESLint warnings" | Every CI lint, format or type-check job passed | A lint job failed |
| Tests added (`TESTS_ADDED`) | "Added unit tests for the parser" | Test files changed | Never: tests can live inside code files |
| Docs updated (`DOCS_UPDATED`) | "Updated the README" | Documentation files changed | Never: docs can live inside code |
| Fixes an issue (`FIXES_ISSUE`) | "Fixes #123", "Closes owner/repo#45" | The merge closed the issue and it stayed closed for 30 days | The issue is still open after a merge into the default branch, or was reopened and is open |
| Deployed (`DEPLOYED`) | "Deployed to staging" | A deployment of the commit to that environment succeeded | A deployment failed, none is still running, and GitHub listed every deployment of the commit |
| The merge holds (`MERGE_HOLDS`) | Implicit in every merged PR | No revert within 30 days | A revert of the merge on the base branch, not itself reverted |

A claim about a local run ("tests pass locally") or part of the suite ("unit tests for X pass")
is never contradicted by failing CI, because local environments and the rest of the suite differ.

`FIXES_ISSUE` is contradicted only when GitHub itself would have closed the issue: a closing
keyword in the body or a commit message, for an issue in the same repository. A reference in a
title, a comment or another repository is not linked by GitHub, so an open issue there is not
evidence against the claim.

## Verdicts

| Verdict | Meaning |
|---|---|
| verified | The evidence agrees with the claim. |
| contradicted | GitHub's record clearly disagrees with the claim. |
| pending | Too early to tell: CI is running, the PR is not merged yet, or a durability claim is still being watched. |
| unverifiable | The evidence cannot settle it: missing, expired, ambiguous or unreadable. |

Missing or ambiguous evidence always gives unverifiable, never contradicted. The complete list
of ways to reach contradicted, with their exceptions, is the table in
[packs/coding.md](packs/coding.md); a test keeps that table and the code in step.

## Integrity guards and cautions

A pull request can make CI pass by weakening the tests. When CI passed but the same pull
request did one of the following, the verification is withdrawn: the claim is shown as
"not verified" with the reason, the check is neutral, and it never fails.

| Reason | Shown as |
|---|---|
| `PR_SKIPS_TESTS` | this PR skips tests |
| `PR_WEAKENS_TEST_COMMAND` | this PR weakens how CI runs tests |
| `PR_LOWERS_COVERAGE_GATE` | this PR lowers the coverage gate |
| `ADDED_TESTS_ARE_SKIPPED` | the added tests are skipped |
| `CI_TOLERATES_TEST_FAILURES` | this PR lets CI continue when tests fail |
| `BUILD_SKIPS_TESTS` | this PR makes a build command skip tests |

Cautions are facts worth a reviewer's attention that do not change the result:

| Reason | Shown as |
|---|---|
| `PR_CHANGES_CI_CONFIG` | this PR changed CI or test configuration |
| `PR_REMOVES_TESTS` | this PR removes tests while the code stays |
| `LINT_SUPPRESSIONS_ADDED` | this PR adds lint suppressions |
| `LINT_CONFIG_CHANGED` | this PR changed lint configuration |
| `MATCHING_JOB_SKIPPED` | some matching CI jobs were skipped |
| `NOT_INSPECTED` | GitHub's API left out the diff of some files, so they were not inspected |
| `NO_TEST_FILES_FOUND` | no test file found in the changed files (tests inside code files are not detected) |
| `NO_DOC_FILES_FOUND` | no documentation file found in the changed files (docs inside code are not detected) |
| `REOPENED_THEN_CLOSED` | the issue was reopened after the merge, then closed again |
| `REVERT_UNDONE` | the merge was reverted, then the revert was itself reverted |
| `GUARD_ERROR` | an integrity guard could not be evaluated |

## Reasons

Reports show reasons in words; the JSON output keeps the codes.

| Code | Shown as |
|---|---|
| `ALL_MATCHING_CI_JOBS_PASSED` | every matching CI job passed |
| `AWAITING_CI` | waiting for CI to finish |
| `CI_FAILED_LOCAL_CLAIM` | CI failed, but the claim was about a local run |
| `CI_FAILED_SUBSET_CLAIM` | CI failed, but the claim covered only part of the checks |
| `CI_JOB_FAILED` | a matching CI job failed |
| `CI_TIMEOUT` | CI did not finish in time |
| `CLAIM_COMMIT_AMBIGUOUS` | CI failed on only one of the commits the claim could mean |
| `CLOSE_PENDING` | merged; waiting for GitHub to close the issue |
| `DEPLOYING` | a deployment is still running |
| `DEPLOYMENT_FAILED` | every deployment of this commit failed |
| `DEPLOYMENT_FAILED_UNCLEAR` | a deployment failed, but GitHub may not list them all |
| `DEPLOYMENT_SUCCEEDED` | a deployment of this commit succeeded |
| `DEPLOYMENT_UNCLEAR` | the deployments do not settle it |
| `DOC_FILES_CHANGED` | documentation files changed |
| `IN_WINDOW` | still being watched |
| `ISSUE_ALREADY_CLOSED` | the issue was already closed before the PR |
| `ISSUE_CLOSED_BY_MERGE` | the issue was closed when the PR merged |
| `ISSUE_NOT_ACCESSIBLE` | the issue could not be read |
| `ISSUE_OPEN` | the issue is open |
| `ISSUE_REOPENED` | the issue was reopened after the merge |
| `ISSUE_STAYED_CLOSED` | the issue stayed closed |
| `ISSUE_STILL_OPEN` | the issue is still open after the merge |
| `NOT_AUTO_CLOSED` | GitHub does not close issues referenced this way |
| `NOT_DEFAULT_BRANCH` | merged into a branch other than the default |
| `NOT_MERGED` | the PR was not merged |
| `NOT_MERGED_YET` | decided once the PR is merged |
| `NOT_REVERTED` | not reverted |
| `NO_CI_RECORDS` | no CI ran on this commit (or its results were deleted) |
| `NO_CI_RESULTS` | the matching CI jobs were skipped, cancelled or timed out |
| `NO_CLOSE_TIME` | GitHub gave no close time for the issue |
| `NO_COMMIT_REFERENCE` | no commit to check it against |
| `NO_DEPLOYMENT_RECORDS` | no deployment of this commit was found |
| `NO_FILE_LIST` | the changed files could not be read |
| `NO_MATCHING_CI_JOBS` | CI ran, but no job matched this claim |
| `NO_MERGE_TIME` | GitHub gave no merge time |
| `NO_PR_STATE` | the PR's state could not be read |
| `PR_NOT_MERGED` | the PR was closed without merging |
| `REVERTED` | the merge was reverted |
| `SOURCE_STALE` | GitHub's data could not be read |
| `TEST_FILES_CHANGED` | test files changed |
