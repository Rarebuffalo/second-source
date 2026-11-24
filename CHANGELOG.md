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
