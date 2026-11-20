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
