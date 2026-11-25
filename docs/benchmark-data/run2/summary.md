# Agent Claim Truth

Counted per PR: a PR's claims of one type give one outcome. False rate = contradicted
÷ checkable PRs (95% Wilson interval; PRs of one repository are not independent, so
check the *Repos* column). Coverage = checkable ÷ all PRs with the claim. *Scoped, CI
failed* = the agent said a check passed locally, or that part of the checks passed, and
a CI job failed; shown separately and never counted as false. *Withdrawn* = CI passed but
the PR weakened its own tests or CI (integrity guards); *Without guards* counts those as
verified, as runs before the guards did, for comparison. *all* = PRs by their
written claims together: a PR counts as contradicted if any of them was, so it is not
comparable to the per-type rates (MERGE_HOLDS, implicit in every merge, is left out).
Only high-confidence claims (found by extraction rules with proven precision) are
counted.

## Comparable to testreceipt

PRs whose body claims the tests pass (high confidence, unscoped) and whose CI at
the claim's commit decided it; share where CI failed, by year opened. Compare with
testreceipt's study (github.com/amos689/testreceipt): same question, independent
parser and labels.

| Year | PRs | Repos | CI failed | Rate (95% CI) |
|---|---:|---:|---:|---|
| 2025 | 2 | 2 | 0 | 0% (0%–66%) |
| 2026 | 18 | 17 | 0 | 0% (0%–18%) |

## claude_code

198 PRs checked, 2 errors, agent detection agreed with the dataset label on 100%; 92 low-confidence claims not counted.

| Claim | PRs | Repos | Verified | Contradicted | Scoped, CI failed | Withdrawn | Coverage | False rate (95% CI) | Without guards (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| DOCS_UPDATED | 29 | 27 | 27 | 0 | 0 | 0 | 93% | 0% (0%–12%) | 0% (0%–12%) |
| FIXES_ISSUE | 33 | 26 | 26 | 0 | 0 | 0 | 79% | 0% (0%–13%) | 0% (0%–13%) |
| LINT_OK | 11 | 1 | 1 | 0 | 0 | 0 | 9% | 0% (0%–79%) | 0% (0%–79%) |
| MERGE_HOLDS | 198 | 175 | 184 | 1 | 0 | 0 | 93% | 1% (0%–3%) | 1% (0%–3%) |
| TESTS_ADDED | 27 | 23 | 23 | 0 | 0 | 0 | 85% | 0% (0%–14%) | 0% (0%–14%) |
| TESTS_PASS | 54 | 22 | 22 | 0 | 2 | 0 | 41% | 0% (0%–15%) | 0% (0%–15%) |
| all | 102 | 76 | 77 | 0 | 2 | 0 | 75% | 0% (0%–5%) | 0% (0%–5%) |

## codex

197 PRs checked, 3 errors, agent detection agreed with the dataset label on 97%; 24 low-confidence claims not counted.

| Claim | PRs | Repos | Verified | Contradicted | Scoped, CI failed | Withdrawn | Coverage | False rate (95% CI) | Without guards (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| DOCS_UPDATED | 18 | 16 | 16 | 0 | 0 | 0 | 89% | 0% (0%–19%) | 0% (0%–19%) |
| FIXES_ISSUE | 10 | 8 | 8 | 0 | 0 | 0 | 80% | 0% (0%–32%) | 0% (0%–32%) |
| LINT_OK | 2 | 0 | 0 | 0 | 0 | 0 | 0% | – | – |
| MERGE_HOLDS | 197 | 136 | 157 | 0 | 0 | 0 | 80% | 0% (0%–2%) | 0% (0%–2%) |
| TESTS_ADDED | 23 | 21 | 21 | 0 | 0 | 0 | 91% | 0% (0%–15%) | 0% (0%–15%) |
| TESTS_PASS | 7 | 2 | 2 | 0 | 0 | 0 | 29% | 0% (0%–66%) | 0% (0%–66%) |
| all | 50 | 40 | 41 | 0 | 0 | 0 | 82% | 0% (0%–9%) | 0% (0%–9%) |

## copilot

195 PRs checked, 5 errors, agent detection agreed with the dataset label on 100%; 62 low-confidence claims not counted.

| Claim | PRs | Repos | Verified | Contradicted | Scoped, CI failed | Withdrawn | Coverage | False rate (95% CI) | Without guards (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| DOCS_UPDATED | 40 | 37 | 37 | 0 | 0 | 0 | 92% | 0% (0%–9%) | 0% (0%–9%) |
| FIXES_ISSUE | 102 | 89 | 92 | 2 | 0 | 0 | 92% | 2% (1%–7%) | 2% (1%–7%) |
| LINT_OK | 5 | 0 | 0 | 0 | 0 | 0 | 0% | – | – |
| MERGE_HOLDS | 195 | 137 | 143 | 1 | 0 | 0 | 74% | 1% (0%–4%) | 1% (0%–4%) |
| TESTS_ADDED | 39 | 34 | 34 | 0 | 0 | 0 | 87% | 0% (0%–10%) | 0% (0%–10%) |
| TESTS_PASS | 25 | 11 | 11 | 0 | 0 | 0 | 44% | 0% (0%–26%) | 0% (0%–26%) |
| all | 133 | 120 | 123 | 2 | 0 | 0 | 94% | 2% (0%–6%) | 2% (0%–6%) |

## cursor

194 PRs checked, 6 errors, agent detection agreed with the dataset label on 99%; 42 low-confidence claims not counted.

| Claim | PRs | Repos | Verified | Contradicted | Scoped, CI failed | Withdrawn | Coverage | False rate (95% CI) | Without guards (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| DOCS_UPDATED | 19 | 19 | 19 | 0 | 0 | 0 | 100% | 0% (0%–17%) | 0% (0%–17%) |
| FIXES_ISSUE | 10 | 9 | 9 | 0 | 0 | 0 | 90% | 0% (0%–30%) | 0% (0%–30%) |
| MERGE_HOLDS | 194 | 120 | 160 | 2 | 0 | 0 | 84% | 1% (0%–4%) | 1% (0%–4%) |
| TESTS_ADDED | 8 | 6 | 6 | 0 | 0 | 0 | 75% | 0% (0%–39%) | 0% (0%–39%) |
| TESTS_PASS | 7 | 3 | 3 | 0 | 1 | 0 | 43% | 0% (0%–56%) | 0% (0%–56%) |
| all | 36 | 33 | 33 | 0 | 0 | 0 | 92% | 0% (0%–10%) | 0% (0%–10%) |

## devin

194 PRs checked, 6 errors, agent detection agreed with the dataset label on 99%; 51 low-confidence claims not counted.

| Claim | PRs | Repos | Verified | Contradicted | Scoped, CI failed | Withdrawn | Coverage | False rate (95% CI) | Without guards (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| DOCS_UPDATED | 33 | 23 | 31 | 0 | 0 | 0 | 94% | 0% (0%–11%) | 0% (0%–11%) |
| FIXES_ISSUE | 19 | 14 | 16 | 0 | 0 | 0 | 84% | 0% (0%–19%) | 0% (0%–19%) |
| LINT_OK | 15 | 1 | 1 | 0 | 0 | 0 | 7% | 0% (0%–79%) | 0% (0%–79%) |
| MERGE_HOLDS | 194 | 96 | 164 | 2 | 0 | 0 | 86% | 1% (0%–4%) | 1% (0%–4%) |
| TESTS_ADDED | 14 | 10 | 10 | 0 | 0 | 0 | 71% | 0% (0%–28%) | 0% (0%–28%) |
| TESTS_PASS | 20 | 2 | 2 | 0 | 0 | 0 | 10% | 0% (0%–66%) | 0% (0%–66%) |
| all | 81 | 44 | 57 | 0 | 0 | 0 | 70% | 0% (0%–6%) | 0% (0%–6%) |

## jules

197 PRs checked, 3 errors, agent detection agreed with the dataset label on 100%; 52 low-confidence claims not counted.

| Claim | PRs | Repos | Verified | Contradicted | Scoped, CI failed | Withdrawn | Coverage | False rate (95% CI) | Without guards (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| DOCS_UPDATED | 25 | 22 | 23 | 0 | 0 | 0 | 92% | 0% (0%–14%) | 0% (0%–14%) |
| FIXES_ISSUE | 24 | 12 | 16 | 0 | 0 | 0 | 67% | 0% (0%–19%) | 0% (0%–19%) |
| MERGE_HOLDS | 197 | 113 | 164 | 1 | 0 | 0 | 84% | 1% (0%–3%) | 1% (0%–3%) |
| TESTS_ADDED | 13 | 10 | 13 | 0 | 0 | 0 | 100% | 0% (0%–23%) | 0% (0%–23%) |
| TESTS_PASS | 7 | 1 | 1 | 0 | 0 | 0 | 14% | 0% (0%–79%) | 0% (0%–79%) |
| all | 66 | 43 | 52 | 0 | 0 | 0 | 79% | 0% (0%–7%) | 0% (0%–7%) |
