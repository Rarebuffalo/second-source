# Security model

Second Source reads text that anyone opening a pull request controls, and the GitHub Action runs
with a token that can write to the repository. This page states what is trusted, what is not,
and the measures that keep the two apart. To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

## Assets

- The workflow's `GITHUB_TOKEN`, with the permissions listed in the
  [Action reference](action.md#permissions).
- The integrity of verdicts: a reviewer should not be misled by a forged or suppressed result.
- The repository's API rate limit, which other workflows share.
- For private repositories, their contents.

## Trust boundaries

| Source | Trusted? |
|---|---|
| The Action's own code and its locked dependencies | Yes, as released and pinned |
| GitHub's API responses | Yes, as the record of what happened |
| Pull request title, body, comments, commit messages, branch names, file paths, diffs | No |
| The workspace of the runner | No |

## Measures

**No pull request code runs.** The Action never checks out the pull request. It installs only its
own hash-locked dependencies from its own repository, without building anything, before the
token is in the environment, and runs from its own source with `PYTHONSAFEPATH=1`, so nothing in
the workspace can be imported. This is why `pull_request_target`, which gives fork pull requests
a writable token, is safe here.

**No model reads the text.** Claims are found by rules and verdicts are decided by checks, so
instructions written into a pull request cannot steer a result.

**Text cannot stall or crash a run.** Whitespace runs are collapsed and blocks removed in linear
time before any pattern is matched, each text is read up to 20,000 characters, numbers are
bounded, and an unexpected error on one pull request is reported by its type only and does not
fail the run. Tests run every known attack shape at full size under a time budget.

**Text cannot spend the API quota.** At most 20 issue references, 8 commits with CI claims and 4
with deploy claims are looked up per pull request, the body's first.

**Text is shown inert.** In the check run, quotes of pull request text are code spans, so HTML,
links, images and mentions in them do nothing; repository names are shown the same way. Control
and bidirectional characters are replaced in terminal output. The reply to a dispute never
repeats the comment.

**Results cannot be frozen by look-alikes.** Only a check run created by GitHub Actions is
updated, and a recorded report time in the future is ignored. An older result never replaces a
newer one.

**The token stays on GitHub.** It is never printed, logged or cached, and it is never sent to a
pagination link or redirect outside the API host.

**Supply chain.** `setup-uv` is pinned to a commit and uv to an exact version with a bundled
checksum; dependencies are hash-locked; the release workflow publishes to PyPI with trusted
publishing and an approval step.

## Known limits

- Every workflow in a repository acts as the same GitHub Actions app, so your own workflows (or
  one changed by a same-repository pull request) can create or overwrite the check run. Fork pull
  requests cannot on public repositories. If you make the check required, protect the branch and
  review changes to `.github/workflows/`, which Second Source flags.
- A pull request body with many decoy references can push its own later claims past the
  per-PR limits; those claims are reported unverifiable, never wrong.
- `@v0` is a moving tag. Pin a commit SHA to run only code you have reviewed.

Two independent security reviews were run before the first release, and their findings were
fixed; the [changelog](../CHANGELOG.md) lists them.
