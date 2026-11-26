# Agent Claim Truth benchmark, run 2: aggregate results

What is here:

- `summary.json`: per agent and per claim type, how many PRs made the claim, how many were
  checkable, verified, contradicted, pending, unverifiable or withdrawn by an integrity guard,
  the coverage, and the false rate with a 95% Wilson interval; plus the cell comparable to
  testreceipt's study, the code version every verdict came from, and the number of verdicts a
  re-score could not reproduce (0).
- `summary.md`: the same tables, readable.

How it was made: 1,200 merged agent pull requests drawn from the AIDev dataset (200 per agent:
Claude Code, Codex, Copilot, Cursor, Devin, Jules; at most 3 per repository), checked against
GitHub with `second-source bench`, then re-scored offline on one code version (Second Source
0.1.0, coding pack 0.5.0). 25 PRs were no longer on GitHub, leaving 1,175. Method, caveats and
findings: [docs/benchmark.md](../../benchmark.md), sections 5, 6 and 9.

What is deliberately not here: per-PR verdicts. Several agents' PRs are opened by people, and
a row saying "contradicted" next to a PR names its author; only aggregates are published.

Read the numbers with the caveats in docs/benchmark.md section 9: contradictions are a floor,
not a rate; only merged PRs were sampled; PRs within one repository are not independent; and
most older "tests pass" claims could not be checked because GitHub had deleted the CI runs.

Licence: derived from AIDev (Hao Li et al., [hao-li/AIDev](https://huggingface.co/datasets/hao-li/AIDev)),
CC-BY-4.0; these aggregates are released under CC-BY-4.0 too.
