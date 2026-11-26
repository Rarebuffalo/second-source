# Quickstart

This guide adds Second Source to a repository and shows how to read its first result. It takes
about five minutes. You need write access to the repository and [uv](https://docs.astral.sh/uv/).

## Add the Action

In a clone of the repository, run:

```bash
uvx second-source init
```

`init` writes `.github/workflows/second-source.yml`. It reads `.github/workflows` to find your
CI workflows and lists them under `workflow_run`, so a pull request's check updates as soon as
CI finishes. Review the file, then commit and push it to the default branch:

```bash
git add .github/workflows/second-source.yml
git commit -m "Add Second Source"
git push
```

The workflow runs on `pull_request_target`, which uses the workflow file from the default
branch, so it has no effect until it is merged there.

## See the first check

Open, or update, a pull request made by a coding agent. A check named "Second Source" appears
next to your CI. While CI is running it shows "Waiting for CI"; when CI finishes it shows a
result such as "2 verified, 1 pending".

Open the check to see:

- a table of the agent's claims, each with its verdict, the reason and the sentence it came
  from;
- "Evidence at a glance": CI on the head commit, what the change touched, and merge and revert
  state.

[Claims and verdicts](claims.md) explains each verdict and reason. By default the check never
fails, so it does not block merges.

## If a verdict looks wrong

Comment `/second-source dispute <why>` on the pull request. The check is marked as disputed at
once, and the Action replies with a link to report it. See [Troubleshooting](troubleshooting.md)
for common cases.

## Check one pull request from the command line

```bash
uvx second-source check https://github.com/OWNER/REPO/pull/123
```

A token is picked up from `GITHUB_TOKEN`, `GH_TOKEN` or a logged-in GitHub CLI; without one,
the public API allows about 10 checks an hour. The [CLI reference](cli.md) lists every option.

## Next steps

- Tune the Action with its [inputs](action.md#inputs), for example `ignore-claims` to switch off
  a claim type, or `fail-on-contradiction` to make contradictions fail the check.
- Pin the Action to a commit if you want to run only reviewed code; see
  [Versioning and pinning](action.md#versioning-and-pinning).
