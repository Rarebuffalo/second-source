# GitHub Action design (milestone M4)

The Action is the free, zero-setup way to use Second Source: a repository adds one workflow
file, and every pull request opened by a coding agent gets a **"Second Source" check** listing
the agent's claims and whether each holds up. No server, no account, no data leaves GitHub.

This document fixes the design before code. Goals, in order: never accuse wrongly, cost the
user nothing, need no manual work beyond adding the workflow.

## 1. Shape

| Decision | Choice | Why |
|---|---|---|
| Action type | **Composite** action using `astral-sh/setup-uv`, then `uv run --project $GITHUB_ACTION_PATH second-source action` | No Docker image to build or pull; `uv` caches dependencies, so a run takes seconds |
| State | **None.** Every run rebuilds the verdict from GitHub | Stateless means no database, nothing to host, nothing to leak |
| Output | A **check run** named "Second Source" on the PR's head commit, plus the job summary | Check runs show next to CI, support Markdown, and can be updated in place |
| Token | The workflow's own `GITHUB_TOKEN` | No secrets to create. Never printed; the CLI's token handling is reused |

## 2. When it runs

The recommended workflow listens to four events. Each maps to one mode:

| Event | Mode | What happens |
|---|---|---|
| `pull_request_target` (opened, edited, synchronize, reopened) | **check** | Check this PR. If CI has not finished, wait for it (see 3) |
| `workflow_run` (completed) | **check** | CI finished: re-check the PRs whose head commit that run built |
| `schedule` (daily) | **sweep** | Re-check agent PRs updated or merged in the last 30 days: durability (reverts, reopened issues) and anything still pending |
| `issue_comment` (created) | **command** | `/second-source recheck` or `/second-source dispute <reason>` on a PR |

**Why `pull_request_target`.** With plain `pull_request`, PRs from forks get a read-only
token and cannot create check runs. `pull_request_target` runs with the base repository's
token. It is dangerous only for workflows that check out and run the PR's code. **Second Source
never checks out or runs PR code**; it reads the PR through the API only, and it installs itself
from the action's own repository, not from the PR, and never reads the workspace. The setup
docs say this plainly and warn against adding a checkout step to this workflow.

**Only agent PRs.** By default PRs with no detected agent are skipped with a one-line note, so
human PRs are never labelled. Input `only-agents: false` checks every PR.

## 3. Waiting for CI

Claims like "tests pass" can only be judged once CI finishes. Two mechanisms, both free:

1. **Short wait.** In check mode, if any verdict is `PENDING` with reason `AWAITING_CI`, poll
   with backoff for up to `wait-minutes` (default 10). Public repositories have free Actions
   minutes; private repositories can set `wait-minutes: 0`.
2. **Event follow-up.** `workflow_run: completed` re-checks after CI ends, and the daily sweep
   catches anything left. The check run is updated in place, so the PR always shows the latest
   verdict.

While anything is pending, the check run stays `in_progress` with the current table, so people
see progress instead of nothing.

## 4. Check run conclusion

| Verdicts | Conclusion | Title |
|---|---|---|
| any high-confidence `CONTRADICTED` | `neutral` (default) or `failure` (with `fail-on-contradiction: true`) | "1 claim contradicted" |
| any `PENDING` | stays `in_progress` | "Waiting for CI" / "Watching for 30 days" |
| a high-confidence claim withdrawn by an integrity guard (CI passed, but the PR skipped tests, weakened its test command or lowered the coverage gate) | `neutral`, **never** `failure`, whatever `fail-on-contradiction` says | "1 claim not verified: this PR skips tests" |
| otherwise | `success` | "4 claims verified, 1 unverifiable" |

Integrity cautions (CI config changed, tests removed, lint suppressions…) appear in the table
next to the result and never change the conclusion. See docs/packs/coding.md, "Integrity
guards".

The default never blocks a merge: Second Source starts as advice, not a gate, until a team
chooses otherwise. Low-confidence contradictions are shown but never change the conclusion.

## 5. Commands (from PR comments)

- `/second-source recheck`: run check mode now.
- `/second-source dispute <reason>`: the person thinks a verdict is wrong. The Action adds the
  label `second-source: disputed`, replies with a short acknowledgement and a pre-filled link to
  the project's "wrong verdict" issue form, and from then on shows the dispute in the check run.
  Stateless: the label is the record.
- Only the PR author or users with write access (`OWNER`, `MEMBER`, `COLLABORATOR`) can run
  commands; other comments are ignored silently.

## 6. Inputs

| Input | Default | Meaning |
|---|---|---|
| `github-token` | `${{ github.token }}` | Token for reading the PR and writing the check run |
| `wait-minutes` | `10` | How long check mode waits for CI |
| `only-agents` | `true` | Skip PRs with no detected coding agent |
| `fail-on-contradiction` | `false` | Make a contradicted claim fail the check |
| `sweep-days` | `30` | How far back the daily sweep looks |

Required permissions: `checks: write`, `contents: read`, `pull-requests: read`, `issues: write`
(only for the dispute label and reply), `actions: read` (to map `workflow_run` to PRs).

## 7. Setup with minimal manual work

- `second-source init` writes `.github/workflows/second-source.yml` with the events,
  permissions and inputs above, so setup is one command and one commit.
- The README and the Marketplace listing show the same file for copy and paste.

## 8. Code layout

```
src/second_source/action/
  inputs.py    read and validate inputs from the environment
  event.py     GitHub event payload -> mode + target PRs (+ command)
  wait.py      poll until CI settles or the wait budget ends (injectable clock and sleep)
  publish.py   check run create/update, conclusion mapping, job summary
  commands.py  parse /second-source commands, permission check, dispute
  sweep.py     find recent agent PRs for the daily run
  main.py      `second-source action` entrypoint tying the above together
action.yml     the composite action
```

`GitHubClient` gains write requests (`POST`, `PATCH`) without caching or retries on client
errors. Everything is tested against the fake GitHub API used by the CLI tests.

## 9. Build order (one commit per step)

1. This document.
2. Client write requests.
3. Inputs and event parsing.
4. Publishing: conclusion mapping, check run, job summary.
5. Waiting for CI.
6. The entrypoint and `action.yml`.
7. Sweep mode.
8. Commands and disputes.
9. `second-source init`, and the setup section of the README.
