# Real golden set for claim extraction

`cases.jsonl` holds 402 pull request descriptions written by six coding agents (67 each:
Claude Code, Codex, Copilot, Cursor, Devin, Jules), labelled with the claims they make.
Method: docs/benchmark.md, section 3. Labelling rules: [GUIDE.md](GUIDE.md).

## How it was built

1. Sample, on AIDev revision `b8dc67df93e7c11f6f20c84129db7232eb1ec253`:
   - round 1 (120 PRs): `second-source bench sample --per-agent 20 --seed 20261120 --filter has_body`
   - round 2 (282 PRs): `second-source bench sample --per-agent 47 --seed 20261121 --filter has_body --exclude-from cases.jsonl`
2. Two labellers labelled every body independently from the guide, without seeing the
   extraction rules or each other's labels (`labeller_a`, `labeller_b` in each record).
   **Who labelled:** both labellers were AI model sessions (Claude subagents), each given only
   the guide and the texts. No person labelled the set. This is ground truth made by a model,
   not a model in the verdict path: Second Source's verdicts stay rule-based. Because both
   labellers are the same model, their agreement overstates independence; a human audit of a
   random subset is planned and will be reported here.
3. Where they disagreed, the building session read the case against the guide and decided
   `expected`; the reason is in `adjudication`. `second-source bench merge-labels` rebuilds the cases from the labeller files.
4. Each case is in the `dev` or `test` split by a hash of its URL
   (`second_source.benchmark.golden.split_for`). Rules are tuned on `dev` only; `test` is
   scored once, to report.

Score it yourself:

```bash
second-source bench golden tests/packs/coding/golden/real/cases.jsonl --split dev
```

## Attribution and licence

The pull request texts come from the AIDev dataset by Hao Li et al.
(<https://huggingface.co/datasets/hao-li/AIDev>), licensed CC-BY-4.0, and were written in
public GitHub repositories linked from each record. They are redistributed here unchanged
under the same licence. The labels are ours.

## Results

Labeller agreement before adjudication: 113 of 120 PRs exactly equal in round 1, 272 of 282 in
round 2 (385 of 402 overall, 96%). Exact agreement is inflated when most PRs have no claim of a
type, so Cohen's kappa per type (presence, chance-corrected; `bench merge-labels` prints it):

| Type | kappa |
|---|---|
| TESTS_PASS | 0.980 |
| BUILD_OK | 0.966 |
| LINT_OK | 0.966 |
| TESTS_ADDED | 0.985 |
| FIXES_ISSUE | 0.991 |
| DOCS_UPDATED | 0.925 |

### Round 2 (402 PRs; dev 212, test 190)

Held-out test split, scored once with the rules before and after round-2 tuning on the dev
split. Precision / recall:

| Claim type | Labelled | Before round 2 | After round 2 | Dev split after |
|---|---:|---|---|---|
| TESTS_PASS | 27 | 0.85 / 0.81 | **0.80** / 0.89 | 0.97 / 0.93 |
| BUILD_OK | 20 | 0.90 / 0.45 | 0.91 / 0.50 | 1.00 / 0.55 |
| LINT_OK | 17 | 1.00 / 0.65 | 1.00 / 0.65 | 1.00 / 0.86 |
| TESTS_ADDED | 38 | 0.87 / 0.71 | 0.87 / 0.68 | 0.97 / 0.79 |
| FIXES_ISSUE | 33 | 1.00 / 0.94 | 1.00 / 0.94 | 1.00 / 0.97 |
| DOCS_UPDATED | 42 | 0.87 / 0.31 | 0.85 / 0.40 | 1.00 / 0.65 |

What this says:

- Tuning on 212 dev cases still does not carry over to unseen text for TESTS_PASS: dev
  precision rose to 0.97 while test precision fell from 0.85 to 0.80. New, looser phrasings
  ("verified ... tests", "tests ... all pass") raised recall but also false claims.
- FIXES_ISSUE (1.00) and LINT_OK (1.00) are reliable on unseen text; BUILD_OK and TESTS_ADDED
  are close to 0.9; DOCS_UPDATED and TESTS_PASS are not yet at the 0.95 target.
- Next step: measure precision **per extraction rule** and let the benchmark use only rules
  that hold the target on both splits, instead of adding more rules.

### Trusted rules (per-rule precision)

Each extraction rule is scored on its own (`second-source bench rules`). Rules that fired at
least 5 times on the dev split with precision ≥ 0.95 were marked trusted
(`src/second_source/packs/coding/trusted_rules.yaml`, 10 rules); their claims are "high
confidence". The selection was then checked once on the test split:

| Claim type | Test precision, all claims | Test precision, high confidence only | Recall, high only |
|---|---|---|---|
| FIXES_ISSUE | 1.00 | **1.00** | 0.94 |
| LINT_OK | 1.00 | **1.00** | 0.29 |
| TESTS_PASS | 0.80 | 0.88 | 0.78 |
| TESTS_ADDED | 0.87 | 0.90 | 0.68 |
| DOCS_UPDATED | 0.85 | 0.83 | 0.36 |
| BUILD_OK | 0.91 | no trusted rule yet | 0.00 |

What this says:

- Restricting to trusted rules helps TESTS_PASS (0.80 → 0.88) and TESTS_ADDED, and keeps
  FIXES_ISSUE and LINT_OK at 1.00, but rules that looked perfect on dev still lose precision
  on unseen text. Even "tests pass" itself is 0.86 on the test split: whether a sentence is a
  real claim often depends on context (hedges, failures mentioned elsewhere) that patterns do
  not see.
- So the benchmark publishes contradiction rates only for high-confidence claims, and every
  contradicted claim is checked by a person before publication (docs/benchmark.md).

### Round 1 (120 PRs; dev 62, test 58)

Held-out **test split** (58 PRs), scored once before and once after the M3 rule changes that
were tuned on the dev split. Precision / recall per claim type:

| Claim type | Labelled | Before M3 | After M3 |
|---|---:|---|---|
| TESTS_PASS | 9 | 0.62 / 0.56 | 0.75 / 0.67 |
| BUILD_OK | 7 | 1.00 / 0.57 | 0.83 / 0.71 |
| LINT_OK | 6 | 1.00 / 0.17 | 1.00 / 0.50 |
| TESTS_ADDED | 13 | 1.00 / 0.23 | 1.00 / 0.54 |
| FIXES_ISSUE | 5 | 1.00 / 1.00 | 1.00 / 1.00 |
| DOCS_UPDATED | 11 | 0.50 / 0.09 | 0.83 / 0.45 |

What this says:

- Recall roughly doubled, but the dev split (62 PRs) scored near 1.00 while the test split did
  not, so the rules partly fit the dev examples. A much larger labelled set is needed before
  precision can be trusted at the 0.95 target.
- TESTS_PASS precision of 0.75 on unseen text means extraction alone can create false claims.
  Until it meets the target, any benchmark "contradicted" result must be checked by a person
  before it is published.
