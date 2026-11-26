# FAQ

## Does Second Source use an AI model?

No. Claims are found with rules and verdicts are decided by checks written in YAML and CEL. No
model reads the pull request, so results are reproducible and cannot be steered by text written
into it.

## Will it block my merges?

Not by default. A contradicted claim makes the check neutral, never failed. Set
`fail-on-contradiction: "true"` to make contradictions fail the check. A verification withdrawn
by an integrity guard is always neutral.

## Is it free?

Yes. It is open source under Apache-2.0 and runs in your own GitHub Actions. Public repositories
have free Actions minutes; on private repositories each run takes 15 to 45 seconds plus any time
spent waiting for CI (see [cost and limits](action.md#cost-and-limits)).

## What data leaves my repository?

None. The Action and the CLI talk only to the GitHub API. Nothing is sent to the project or to
anyone else. The CLI caches API responses on your machine, never including code.

## Which agents are supported?

Claude Code, Codex, Copilot, Cursor, Devin and Jules, detected from the author, branch name, body
or commit trailers. Set `only-agents: false` to check every pull request.

## Is it ready for production use?

It is a beta. It has been tested on a sandbox repository and on a benchmark of 1,175 agent pull
requests, and reviewed for security, but interfaces may change before 1.0. Run it as advice
first; [Accuracy](accuracy.md) describes where it is strong and where it is not.

## How is this different from looking at CI myself?

CI tells you whether jobs passed now. Second Source ties each claim to the commit it was made on,
keeps watching after the merge (did the issue stay closed, was the change reverted), notices
when a pull request weakens its own tests, and records the evidence while it still exists;
GitHub deletes old CI runs.

## Can I add my own checks or claim types?

Checks are YAML files with CEL expressions in the coding pack. See
[CONTRIBUTING.md](../CONTRIBUTING.md) and the [coding pack](packs/coding.md); changes to
extraction rules must keep their measured precision.

## Why is the Marketplace listing called "Second Source Claim Check"?

Marketplace names must be unique across all actions, users and organisations, and a GitHub
account named `second-source` exists. The project, the package and the check are all called
Second Source.
