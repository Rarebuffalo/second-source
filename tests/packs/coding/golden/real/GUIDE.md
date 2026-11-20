# Labelling guide for the real golden set

You are labelling the description (body) of a pull request written by an AI coding agent. For
each PR, list the **claims** the text makes: statements that something about this change is
already true and that someone could check. Label what a careful reader would say the text
asserts. Do not guess what a tool might detect.

## Claim types

| Type | The text asserts that... | Examples that count |
|---|---|---|
| `TESTS_PASS` | the project's tests, or some of them, ran and passed on this change | "All tests pass", "`pytest`: 201 passed", "✅ `npm test`", "- [x] Unit tests pass", "CI is green" |
| `BUILD_OK` | the project builds, compiles or type-checks successfully | "Build succeeds", "`cargo build` passes", "compiles without errors", "`tsc --noEmit` passes" |
| `LINT_OK` | linters or formatters ran clean | "Lint passes", "`ruff check` clean", "no ESLint warnings", "pre-commit hooks passed", "formatting checks pass" |
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
5. **Type-checking** (mypy, tsc, pyright) counts as `BUILD_OK`, not `LINT_OK`. **Formatting**
   checks (prettier, black, `ruff format --check`, gofmt) count as `LINT_OK`.
6. **Counting.** Each type counts at most once per PR, except `FIXES_ISSUE`, which counts once
   per distinct issue. If the text says tests pass in three places, that is one `TESTS_PASS`.
7. **Issue references need a closing word.** "Fixes / closes / resolves #N" (any tense) count.
   "Related to #N", "part of #N", "see #N" and links to other PRs do not.
8. **Ignore** text inside fenced code blocks, quoted lines (`>`), HTML comments, and
   boilerplate the agent did not write about this change (for example a repository's PR
   template headings with nothing filled in).
9. **The title** is not part of what you label; label the body only.
10. **When genuinely unsure** whether a sentence asserts something, do not label it, and say
    why in the notes field. Ambiguous cases are useful to know about.

## Output per PR

```yaml
- url: https://github.com/owner/repo/pull/123
  expected: [TESTS_PASS, LINT_OK]
  notes: "tests local; lint stated in Testing section"
```
