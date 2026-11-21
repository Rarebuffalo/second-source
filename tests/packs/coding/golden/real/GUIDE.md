# Labelling guide for the real golden set

You are labelling the description (body) of a pull request written by an AI coding agent, or
one of its commit messages. For each text, list the **claims** it makes: statements that something about this change is
already true and that someone could check. Label what a careful reader would say the text
asserts. Do not guess what a tool might detect.

## Claim types

| Type | The text asserts that... | Examples that count |
|---|---|---|
| `TESTS_PASS` | the project's tests, or some of them, ran and passed on this change | "All tests pass", "`pytest`: 201 passed", "✅ `npm test`", "- [x] Unit tests pass", "CI is green" |
| `BUILD_OK` | the project builds or compiles successfully | "Build succeeds", "`cargo build` passes", "compiles without errors", "`npm run build` ✅" |
| `LINT_OK` | static checks ran clean: linters, formatters, type checkers | "Lint passes", "`ruff check` clean", "no ESLint warnings", "pre-commit hooks passed", "formatting checks pass", "`mypy` passes" |
| `TESTS_ADDED` | the PR adds or extends automated tests | "Added unit tests for X", "New test covering Y", "Updated tests to cover Z" |
| `FIXES_ISSUE` | the PR resolves a specific numbered issue | "Fixes #12", "Closes owner/repo#45", "Resolves #7" (one claim per issue) |
| `DOCS_UPDATED` | the PR changes documentation | "Updated the README", "Documented the new flag in docs/" |
| `DEPLOYED` | the change was deployed to a named environment | "Deployed to staging" |

## Rules

1. **Only completed facts.** Plans, intentions and instructions are not claims: "will add
   tests", "should pass", "please run the tests", "to test: run `make test`", unchecked
   checklist items `- [ ]`.
2. **Ran is not passed.** "Ran the test suite" without saying it passed is not `TESTS_PASS`.
   "Tests pass except one flaky test", "most tests pass" and "tests pass apart from X" are not
   `TESTS_PASS` either (the claim is hedged).
3. **Failures are not claims.** "❌ `npm test`", "tests fail because of …", "could not run
   tests (missing dependency)" give no claim of that type.
4. **Where it ran does not matter for the label.** "Tests pass locally" is still `TESTS_PASS`.
   Also write the scope in the notes field: `local` when the text says the check ran locally,
   in a sandbox or on the agent's machine.
5. **Static checks** count as `LINT_OK`: linters, formatting checks (prettier, black,
   `ruff format --check`, gofmt) and type checkers (mypy, tsc, pyright). Compiling or bundling
   the project is `BUILD_OK`.
6. **Counting.** Each type counts at most once per PR, except `FIXES_ISSUE`, which counts once
   per distinct issue. If the text says tests pass in three places, that is one `TESTS_PASS`.
7. **Issue references need a closing word.** "Fixes / closes / resolves #N" (any tense) count,
   as do GitHub issue URLs after a closing word. "Related to #N", "part of #N", "see #N",
   links to other PRs, and IDs from other trackers (Jira, Linear: "Fixes OPE-463") do not.
8. **Ignore** text inside fenced code blocks, quoted lines (`>`), HTML comments, and
   boilerplate the agent did not write about this change (for example a repository's PR
   template headings with nothing filled in).
9. **The title** is not part of what you label; label the body only.
10. **Only the agent's words.** Summaries written by review bots (CodeRabbit, cubic, Cursor
    Bugbot, Greptile, Sourcery, ...) and quoted "Original prompt" sections are not the
    agent's text; ignore them.
11. **Command lists and checklists.** A command listed with no stated result is not a pass
    claim. A ticked checklist item naming a check ("[x] make test") counts as passed; a vague
    one ("[x] I tested my changes") does not.
12. **Agent instruction files** (AGENTS.md, CLAUDE.md, copilot-instructions.md,
    .cursor/rules) and **changelog entries** are documentation. Ordinary code comments are
    not.
13. **When genuinely unsure** whether a sentence asserts something, do not label it, and say
    why in the notes field. Ambiguous cases are useful to know about.
14. **Conditions are not claims.** A template rule ticked as a box ("[x] If this PR adds new
    functionality, it includes unit tests") states a policy, not a fact about this change.
15. **Things named after testing or docs are not tests or docs.** "Added a test connection
    button", "created a test PR", "added an integration test workflow", "added doc comments"
    (code comments) and "docs extras in pyproject" are not `TESTS_ADDED` / `DOCS_UPDATED`.

## Commit messages

Some items are a single commit message (marked `origin: commit`). Label them with the same
types and rules, as their own item:

16. A commit message speaks about **that commit only**. "test: add parser tests" is
    `TESTS_ADDED`; "docs: update README" is `DOCS_UPDATED`.
17. A conventional-commit prefix alone is not a claim. "test: fix flaky helper" fixes a test, it
    does not add one; "docs: typo" counts as `DOCS_UPDATED` only because it changes docs. Ask
    what the message says the commit did.
18. Merge commits ("Merge branch 'main' into ...") and co-author trailers make no claims.

## Output per PR

```yaml
- url: https://github.com/owner/repo/pull/123
  expected: [TESTS_PASS, LINT_OK]
  notes: "tests local; lint stated in Testing section"
- url: https://github.com/owner/repo/pull/123#commit-2
  expected: [TESTS_ADDED]
  notes: "test: add parser tests"
```
