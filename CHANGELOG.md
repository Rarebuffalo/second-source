# Changelog

All notable changes to Second Source. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Before 1.0, a minor version may change behaviour;
the GitHub Action is used through the moving major tag (`@v0`), which `second-source init`
writes for you.

## [Unreleased]

Working towards the first public release, 0.5.0 (a beta).

### Added
- Engine: claims → evidence → YAML + CEL checks → verdicts (VERIFIED, CONTRADICTED, PENDING,
  UNVERIFIABLE), with reasons and an input hash per check. No AI model decides a verdict.
- Coding pack: "tests pass", "build succeeds", "lint passes", "tests added", "docs updated",
  "fixes #N" (closed, and stayed closed), "deployed", and "the merge holds" (not reverted).
- Integrity guards: a verified claim is withdrawn when the PR skips tests, weakens its test
  command or lowers a coverage gate; cautions for CI-config changes, removed tests and lint
  suppressions. Guards never contradict.
- CLI: `second-source check <PR URL>`, `second-source init`, and `second-source bench`
  (sample, run, summarize, facts, rescore) for the Agent Claim Truth benchmark.
- GitHub Action: one check run per PR, updated as CI finishes; daily durability sweep;
  `/second-source recheck` and `/second-source dispute` commands. Advice by default: never
  fails a check unless `fail-on-contradiction` is set.
- Benchmark run 2 findings (docs/benchmark.md, section 11).

### Fixed (from the pre-launch review)
- A claim no longer shows "Waiting for CI" for hours when the commit's CI finished without a
  matching job, or has no CI at all; it does wait while GitHub Actions workflows on the commit
  are queued, waiting on `needs:` jobs or held for approval (Copilot and fork PRs).
- An older report no longer replaces a newer check run when runs on one PR overlap; CI
  re-checks share the PR's concurrency group, comment commands are never dropped from the
  queue, and one wait budget covers a whole run, within the job's timeout.
- The daily sweep keeps API requests for PR events, starts at a different PR each day, and
  stops at the rate limit instead of failing; a report cut short by the rate limit is never
  published.
- `second-source init` writes the Action tag that matches its own version (`@v0`).

### Fixed (from live testing on a sandbox repository)
- A CI job GitHub reports with a conclusion but a stale `in_progress` status counts as finished.
- A check is grey, titled "(a CI job failed)", when CI failed behind a claim that is only
  unverifiable ("tests pass locally", a partial run, a failure on one of two commits).
- Watched claims say what is watched: "issue closed; watching for a reopen", "not reverted so
  far; watching for a revert".
- The daily sweep finds agent PRs known only from a commit trailer.

- "Fixes #N" is never contradicted in the minutes before GitHub closes the issue after the
  merge (the durability check lacked the close tolerance).

### Security (independent security review before release)
- The Action installs only its hash-locked dependencies and runs from source; it no longer
  builds itself, which fetched an unpinned build backend while the write token was present.
- PR text can no longer stall a run (linear-time cleaning, bounded patterns, a 20,000-character
  read per text), crash it (bounded numbers, a per-PR catch-all), or spend the repository's API
  quota (at most 20 issue references, 8 CI commits and 4 deploy commits per PR; beyond that the
  claims are unverifiable).
- PR text is shown inert: quotes are code spans in the check table, control characters are
  stripped in terminal output, and the dispute reply never repeats the commenter's words and is
  posted once per PR. The report link is no longer prefilled with a possibly private PR.
- Only a check run created by GitHub Actions is updated, and a future report time is ignored, so
  a look-alike check cannot freeze the real one.
- The CLI cache never stores diff patches, and its files are owner-only. Caches written by
  earlier versions may hold patches: delete `~/.cache/second-source` to remove them.
- The token is never sent to a pagination link off the API host; the job summary stays under
  GitHub's 1 MiB limit.
- The Action pins `astral-sh/setup-uv` to a commit and uv to an exact version whose checksum
  that release bundles, and keeps setup-uv's dependency cache off (it can be poisoned on
  `pull_request_target` and `workflow_run`).
