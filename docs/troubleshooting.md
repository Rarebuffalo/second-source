# Troubleshooting

## The check does not appear

- The workflow file must be on the default branch: `pull_request_target` always uses the
  default branch's copy.
- By default only pull requests with a detected coding agent are checked. The run's log says
  "no coding agent detected, skipped". Set `only-agents: false` to check every pull request.
- Check the workflow run in the Actions tab. A run that failed before publishing shows the
  error there; a bad input, such as an unknown name in `ignore-claims`, fails the run with a
  message that names the input.

## The check says "Waiting for CI" for a long time

The check waits while a claim can still be decided: a matching CI job is running, or a GitHub
Actions workflow on the commit is unfinished.

- Copilot and fork pull requests are held until someone with write access approves the
  workflow run ("Approve and run workflows"). Until then CI has not run, and the check waits.
- Any unfinished workflow on the commit holds the wait, including ones unrelated to tests.
- A claim waits at most 6 hours. After that it is decided with what is there.

If the run's job was cancelled or timed out, the check can stay in progress until the next event
or the daily run. Comment `/second-source recheck` to update it now.

## The check updates only after a long time

The `workflow_run` trigger lists your CI workflows by name. A CI workflow added or renamed after
`second-source init` is not in the list, so the check is not updated when it finishes. Add its
name under `workflow_run`, or run `second-source init --force` and review the diff.

## "No claims found"

Most agent pull requests make no claim that can be checked. The check still shows the evidence:
CI on the head commit, what the change touched, and merge and revert state. A claim that is
only in a pull request's linked issue, or in a reviewer's comment, is not the agent's and is not
read.

## A claim is unverifiable

The reason says why. The most common:

| Reason | What to do |
|---|---|
| no CI ran on this commit (or its results were deleted) | Expected on repositories without CI, and on old pull requests whose CI runs GitHub has deleted. |
| CI ran, but no job matched this claim | Second Source recognises test, build and lint jobs by name. Name test jobs with "test" (for example `test`, `unit-tests`, `pytest`). |
| GitHub does not close issues referenced this way | "Fixes #12" in a title or comment, or for another repository, is not linked by GitHub; put it in the body with a closing keyword. |
| CI failed, but the claim was about a local run | Not a contradiction: local and CI environments differ. |

## A verdict looks wrong

1. Comment `/second-source dispute <why>` on the pull request. The check is marked as disputed
   at once.
2. Report it with the
   [wrong-verdict form](https://github.com/Rarebuffalo/second-source/issues/new?template=wrong_verdict.yml).
   For a private repository, describe the case without linking it.
3. If a claim type is consistently wrong for your repository, switch it off with
   `ignore-claims`.

## Rate limits

Each check uses about 10 to 15 API requests from the repository's hourly `GITHUB_TOKEN` limit of
1,000. The daily run stops while 200 requests remain. If a run reaches the limit, it stops with
a warning and never publishes a partial result; the next event or daily run continues.

For the command line, set `GITHUB_TOKEN` or log in with the GitHub CLI: without a token, GitHub
allows about 10 checks an hour.
