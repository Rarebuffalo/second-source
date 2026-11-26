# CLI reference

The `second-source` command checks pull requests, sets up the GitHub Action and runs the
benchmark. Run it without installing, with [uv](https://docs.astral.sh/uv/):

```bash
uvx second-source --help
```

or install it with `uv tool install second-source` or `pipx install second-source`.
It requires Python 3.12 or later.

## `second-source check`

Checks the claims in one pull request and prints a report.

```bash
second-source check https://github.com/OWNER/REPO/pull/123
second-source check OWNER/REPO#123
```

| Option | Description |
|---|---|
| `--json` | Print machine-readable JSON instead of text. |
| `--anonymous` | Never use a token, even if one is available. |
| `--no-cache` | Do not use the on-disk response cache. |
| `--no-color` | Plain text. Colour is also off when `NO_COLOR` is set or output is not a terminal. |

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Checked; no claim is contradicted. |
| `1` | Checked; at least one claim is contradicted (low-confidence claims included; the Action's conclusion counts only high-confidence ones). |
| `2` | Could not check (bad input, pull request not found, network or API error). |

### Authentication

A token is taken, in order, from `GITHUB_TOKEN`, `GH_TOKEN` or the GitHub CLI (`gh auth
token`). For public repositories a token is optional: without one, GitHub allows 60 requests an
hour, about 10 checks. For private repositories the token needs read access to the repository.
Tokens are never printed, logged or cached.

### Cache

Responses are cached with their ETags in `$XDG_CACHE_HOME/second-source/github` (by default
`~/.cache/second-source/github`), so re-checking an unchanged pull request costs almost no
rate limit. Responses that contain code, such as a pull request's file list with its diffs, are
never cached, and cache files are readable only by you.

### JSON output

`--json` prints one object with `schema_version` (currently `"2"`), the pull request, the
detected agent, every claim with its verdict, reasons, caveats and the checks that ran, and the
evidence the verdicts were computed from. Reason codes are listed in
[Claims and verdicts](claims.md#reasons). A new major schema version is announced in the
[changelog](../CHANGELOG.md).

## `second-source init`

Writes the recommended GitHub Action workflow to `.github/workflows/second-source.yml`.

| Option | Description |
|---|---|
| `--path PATH` | Repository root (default: the current directory). |
| `--force` | Overwrite an existing workflow file. |

The workflow is described in the [GitHub Action reference](action.md#workflow).

## `second-source bench`

Runs the Agent Claim Truth benchmark on the AIDev dataset: sampling, checking, re-scoring and
summarising. It is meant for research and for measuring changes to the rules; see the
[benchmark method](benchmark.md). `second-source bench --help` lists the subcommands.

## `second-source action`

The entry point the GitHub Action runs. It reads its settings from the environment GitHub
Actions provides and is not meant to be run by hand.
