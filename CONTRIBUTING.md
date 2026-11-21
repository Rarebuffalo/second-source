# Contributing to Second Source

Thanks for helping. Second Source is early, so the most valuable contributions right now are:

- **Wrong verdicts.** A claim marked contradicted that was true, or verified that was false.
  Open a "Wrong verdict" issue with the PR URL; these directly improve the rules.
- **Missed or invented claims.** Agent wording we do not recognise, or text we read as a claim
  when it is not.
- **Agent detection.** New coding agents, or signals we miss for existing ones.
- **Checks and packs.** New checks for the coding pack, or a pack for a new domain.

## Set up

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Rarebuffalo/second-source && cd second-source
```

```bash
uv sync
```

```bash
./scripts/check.sh
```

`scripts/check.sh` runs ruff lint, the ruff format check and the full test suite. It must pass
before every commit, and CI runs the same checks on Python 3.12 and 3.13.

## How we work

- **Small, focused commits.** One logical change per commit, with a message that says what
  changed and why (`coding: ...`, `github: ...`, `test: ...`, `docs: ...`).
- **Tests with every change.** New behaviour gets a test; a bug fix gets a test that fails
  without the fix. Tests never touch the network: use the fake GitHub API in
  `tests/conftest.py`.
- **Comments explain why.** Every module starts with a docstring saying what it is for;
  non-obvious code gets a short comment explaining the reason.
- **Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first.** The core is domain-neutral and
  pure (no network, clock or filesystem); keep coding-specific logic in `packs/coding`.

## Changing claim extraction

Extraction rules are measured on a labelled golden set of real agent PRs
(`tests/packs/coding/golden/real/`). If you change a rule:

1. Look at mistakes on the **dev** split only:
   `uv run second-source bench golden tests/packs/coding/golden/real/cases.jsonl --split dev`.
2. Add a unit test in `tests/packs/coding/test_extract.py` for the wording you handle.
3. Keep precision at or above the target (0.95); `test_golden_real.py` enforces this on dev.
4. Never tune rules on the **test** split. It exists so our published numbers stay honest.

Precision matters more than recall: a claim the agent never made can become an unfair
"contradicted" verdict.

## Adding a check

Checks are YAML files with CEL expressions in `src/second_source/packs/coding/checks/`. Copy
an existing one, add scenario tests in `tests/packs/coding/test_checks.py`, and remember the
rule: missing or stale evidence gives `UNVERIFIABLE`, never `CONTRADICTED`.

## Code of conduct and security

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Report security
issues privately as described in [SECURITY.md](SECURITY.md), not in public issues.
