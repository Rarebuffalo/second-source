# GitHub source design

The GitHub source turns a pull request into the inputs the engine needs:

- an `Episode`;
- the PR's texts (title, body, commits, comments) for claim extraction;
- the signals used to detect which agent wrote it;
- the evidence records described in the coding pack's evidence contract
  (`docs/packs/coding.md`).

It lives in `src/second_source/sources/github/`.

## 1. Modules

| Module | Responsibility | Pure? |
|---|---|---|
| `urls.py` | Parse `https://github.com/o/r/pull/7` and `o/r#7` | yes |
| `auth.py` | Find a token: `GITHUB_TOKEN` / `GH_TOKEN`, then `gh auth token`, then none | no (env, subprocess) |
| `client.py` | HTTP calls: auth header, pagination, ETag cache, rate limits, retries | no (network) |
| `cache.py` | On-disk ETag cache for the CLI and benchmark | no (filesystem) |
| `mappers.py` | GitHub JSON → Episode, texts, signals, Evidence | yes |
| `source.py` | Orchestration: which endpoints to call for which evidence types | no |

Keeping the mappers pure means almost all logic is tested with JSON fixtures and no network.

## 2. Endpoints

| Need | Endpoint | Calls | Notes |
|---|---|---|---|
| PR, merge state | `GET /repos/{o}/{r}/pulls/{n}` | 1 | Also gives `pr_state` |
| Commits (texts, refs, trailers) | `GET /repos/{o}/{r}/pulls/{n}/commits` | 1–3 | Up to 250 commits |
| Agent comments | `GET /repos/{o}/{r}/issues/{n}/comments` | 1+ | Only comments by the PR author are used as claim texts |
| `check_result` | `GET /repos/{o}/{r}/commits/{ref}/check-runs?filter=latest` and `GET .../commits/{ref}/status` | 2 per referenced commit | `filter=latest` returns the newest run per job |
| `ci_workflow_run` | `GET /repos/{o}/{r}/actions/runs?head_sha={ref}` | 1 per referenced commit | Needs `actions: read`; without it the type is stale and CI waiting falls back to check runs |
| `changed_file` | `GET /repos/{o}/{r}/pulls/{n}/files` | 1+ | Paths and counts only; patches are discarded |
| `issue_state` | `GET /repos/{repo}/issues/{n}` and `GET .../issues/{n}/events` | 2 per referenced issue | Events give the last `reopened` time |
| `revert` | `GET /repos/{o}/{r}/commits?sha={base}&path={one changed file}&since={merged_at}&until={+30 days}` | 1 | Only commits touching one of the PR's files (a full revert must touch it); shares the PR file list with `changed_file`. Skipped for merges into a non-default branch |
| `deployment_status` | `GET /repos/{o}/{r}/deployments?sha={ref}` and statuses | 1 + up to 5 | |

Evidence is fetched **only for the record types the extracted claims need**
(`Engine.evidence_types_for`). A PR with no claims costs about 3 calls; a typical agent PR
costs about 7–11 (CI claims add one workflow-run lookup per commit). PR text cannot raise this
without bound: at most 20 issue references, 8 commits with CI claims and 4 with deploy claims are
looked up, the body's and title's first, then commit messages', then comments'. Claims past the
limit find no evidence (UNVERIFIABLE), and the report says how many were not checked.

## 3. Which commit a text refers to

- **Commit message:** that commit.
- **Title and body:** the newest PR commit dated at or before the PR's creation time; if there
  is none, the first commit.
- **Comment:** the newest PR commit dated at or before the comment.

**Limitation:** commit dates can be misleading after rebases or force-pushes. v1 accepts this.
GraphQL edit history (`lastEditedAt`) can refine body timing later.

## 4. Authentication and privacy

- Tokens are read from the environment or the GitHub CLI. They are never logged, printed or
  written to the cache.
- Without a token, the public API allows 60 requests per hour, which is enough to check a
  few PRs.
- Requests go only to `api.github.com`. The user agent is `second-source/<version>` with no
  personal data.

## 5. Caching

- **ETag cache.** Responses are cached by URL. A repeat request sends `If-None-Match`, and
  GitHub returns `304 Not Modified`, which does not count against the primary rate limit.
- **Location.** The CLI and benchmark store the cache under `$XDG_CACHE_HOME/second-source`
  (default `~/.cache/second-source`). The GitHub Action uses an in-memory cache only.
- **What is stored.** Only response bodies, ETags and URLs. Authorization headers are never
  stored.

## 6. Errors and missing data

| Situation | Behaviour |
|---|---|
| 404 on the PR | Error: "pull request not found or not accessible" |
| 404 on an issue | No `issue_state` record (the check reports `ISSUE_NOT_ACCESSIBLE`) |
| Rate limit (403/429 with remaining 0, or `retry-after`) | Stop fetching; mark the remaining record types **stale** (`SOURCE_STALE`) |
| 5xx or network error | Retry twice with backoff, then mark the record type stale |
| Pagination cap reached | Mark the record type stale rather than judge on partial data |

The principle is the same as in the core: missing or partial data must produce
`UNVERIFIABLE`, never `CONTRADICTED`.

## 7. Testing

- Mappers are tested with JSON fixtures in `tests/sources/github/fixtures/`, which follow
  GitHub's documented response shapes.
- The client is tested with `httpx.MockTransport` for pagination, 304s, rate limits and
  retries.
- The source is tested end to end against a fake transport that serves the fixtures.
- A manual live smoke test runs against a public agent PR. It is not part of CI, because CI
  must not depend on the network.
