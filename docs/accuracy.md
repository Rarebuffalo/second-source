# Accuracy

Two questions matter: does Second Source find the claims an agent really made (extraction), and
are its verdicts right (checking)? This page summarises how each is measured and what the
results are. The full method is in the [benchmark design](benchmark.md).

## Extraction

Extraction is measured on a golden set of 402 real agent pull requests from the
[AIDev](https://huggingface.co/datasets/hao-li/AIDev) dataset, 67 per agent. Every body was
labelled by two labellers working independently from a written guide; they agreed exactly on
96% of pull requests, and disagreements were adjudicated. Both labellers were AI model sessions,
each given only the guide and the text. Because they are the same kind of model, their agreement
overstates independence; a human audit of a sample is planned.

Half the set is used to develop the rules; the other half is scored only to report. A rule is
trusted, and its claims marked high confidence, only if it fired at least five times on the
development half with precision of at least 0.95.

High-confidence precision on the held-out half:

| Claim | Precision |
|---|---|
| Fixes an issue | 1.00 |
| Lint passes | 1.00 |
| Tests added | 0.90 |
| Tests pass | 0.88 |
| Docs updated | 0.83 |

No build-claim rule qualifies yet. Because some types are below 0.95, a contradicted verdict is
best treated as a strong signal for a person to confirm. The labelling guide, the cases and the
per-rule numbers are in [tests/packs/coding/golden/real](../tests/packs/coding/golden/real/README.md).

## Checking: benchmark run 2

1,200 merged agent pull requests were drawn from AIDev, 200 per agent, at most 3 per repository;
1,175 were still on GitHub. Each was checked against GitHub, and every verdict was then
recomputed offline on one code version. The contradictions and a sample of verifications were
audited blind.

- Of 385 pull requests with a written claim that could still be checked, 2 had a claim that
  GitHub's record contradicts. Both were "Fixes #N" with the issue still open after the merge.
- The evidence expires. Every "tests pass" claim that could be verified was under a year old;
  for 42 of the 43 older ones, GitHub had already deleted the CI runs.
- 7 of 979 checkable merges were found reverted.

Read these with their limits. Contradictions are a floor, not a rate: checks contradict only on
unambiguous evidence, and "tests added" and "docs updated" never contradict. Only merged pull
requests were sampled, and pull requests within one repository are not independent. The
aggregate results are published as [open data](benchmark-data/run2/README.md); per-PR verdicts
are not, because a contradicted row would name the pull request's author.

## Live testing

Before release, the Action ran on a sandbox repository with 30 agent pull requests: scripted ones
with deliberately false or gamed claims, and uncoached ones written by fresh agent sessions.
Every verdict matched the expected result, including merges, reverts, a revert of a revert and a
reopened issue.

## Related work

[testreceipt](https://github.com/amos689/testreceipt) checks "tests pass" claims against CI on
AIDev and on 2026 pull requests, and reports CI test failures for 8.7% of 2025 claims and 0.3% of
2026 claims, measured while the CI results still existed. Its figures sit alongside ours rather
than against them; see the [benchmark design](benchmark.md#related-work-and-a-comparable-cell).
