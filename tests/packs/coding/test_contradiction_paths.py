"""CONTRADICTED is the one verdict that accuses an agent, so every way to reach it must be a
deliberate, documented decision (docs/packs/coding.md, "CONTRADICTED comes only from these").

This test ties the documentation table to the check files: a new CONTRADICTED outcome in a
check, or one removed, fails here until the table says the same. Each path's positive and
near-miss behaviour is tested in test_checks.py.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CHECKS = ROOT / "src/second_source/packs/coding/checks"
DOC = ROOT / "docs/packs/coding.md"


def contradicting_outcomes() -> set[tuple[str, str]]:
    paths = set()
    for path in sorted(CHECKS.glob("*.yaml")):
        check = yaml.safe_load(path.read_text(encoding="utf-8"))
        for outcome in check.get("outcomes", []):
            if outcome.get("result") == "CONTRADICTED":
                paths.add((check["id"], outcome["reason"]))
    return paths


def documented_paths() -> set[tuple[str, str]]:
    text = DOC.read_text(encoding="utf-8")
    table = text[text.index("| Claim | Contradicted when | Reason |") :]
    table = table[: table.index("\n\n")]
    return set(re.findall(r"`(coding\.[a-z_]+): ([A-Z_]+)`", table))


def test_the_documented_table_lists_exactly_the_contradiction_paths():
    assert documented_paths() == contradicting_outcomes()


def test_guards_and_optional_checks_never_contradict():
    for path in sorted(CHECKS.glob("*.yaml")):
        check = yaml.safe_load(path.read_text(encoding="utf-8"))
        if check.get("role") == "guard" or check.get("required") is False:
            results = {o.get("result") for o in check.get("outcomes", [])}
            assert "CONTRADICTED" not in results, path.name
