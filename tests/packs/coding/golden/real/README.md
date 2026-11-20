# Real golden set for claim extraction

`cases.jsonl` holds 120 pull request descriptions written by six coding agents (20 each:
Claude Code, Codex, Copilot, Cursor, Devin, Jules), labelled with the claims they make.
Method: docs/benchmark.md, section 3. Labelling rules: [GUIDE.md](GUIDE.md).

## How it was built

1. Sample: `second-source bench sample --per-agent 20 --seed 20261120 --filter has_body`
   on AIDev revision `b8dc67df93e7c11f6f20c84129db7232eb1ec253`.
2. Two labellers labelled every body independently from the guide, without seeing the
   extraction rules (`labeller_a`, `labeller_b` in each record).
3. Where they disagreed, a third read against the guide decided `expected`; the reason is in
   `adjudication`.
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

Labeller agreement before adjudication: 113 of 120 PRs exactly equal.

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
