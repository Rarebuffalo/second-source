"""Rule-based claim extraction for coding agents.

Turns PR text (title, body, commit messages, agent comments) into ``Claim`` objects such as
"TESTS_PASS at commit abc123". No AI model is used: deterministic patterns keep results
cheap, reproducible and resistant to prompt injection.

Guiding rule: **precision over recall.** If a sentence might be a plan, a question, a hedge
or a negation, it is skipped. A missed claim is far less harmful than a claim the agent never
made, because that claim could then be "contradicted" unfairly.

Pipeline per text source:

    clean  ->  split into clauses  ->  drop non-assertions  ->  match patterns  ->  Claims
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from second_source.core.hashing import sha256_hex
from second_source.core.model import Claim
from second_source.packs.coding.functions import (
    is_build_check,
    is_doc_path,
    is_lint_check,
    is_test_check,
    is_test_path,
)

# Claim type names (kept as constants so typos fail loudly in tests and code).
TESTS_PASS = "TESTS_PASS"
BUILD_OK = "BUILD_OK"
LINT_OK = "LINT_OK"
TESTS_ADDED = "TESTS_ADDED"
FIXES_ISSUE = "FIXES_ISSUE"
DOCS_UPDATED = "DOCS_UPDATED"
DEPLOYED = "DEPLOYED"
MERGE_HOLDS = "MERGE_HOLDS"

MAX_SPAN = 200  # characters of supporting text kept on each claim


@dataclass(frozen=True)
class TextSource:
    """One piece of PR text, with when it was written and which commit it refers to."""

    origin: str  # "title" | "body" | "commit" | "comment"
    text: str
    written_at: datetime
    ref: str | None = None  # PR head commit when written (for a commit message: that commit)
    # For editable texts (title, body): the PR head when the text was read, if it differs from
    # ``ref``. The text may have been edited up to then, so either commit could be meant.
    final_ref: str | None = None


# --------------------------------------------------------------------------------------------
# Step 1: cleaning. Remove text that is not the agent's own statement.
# --------------------------------------------------------------------------------------------

_FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_QUOTED_LINE = re.compile(r"^\s*>.*$", re.MULTILINE)


# Review bots (CodeRabbit, Cursor Bugbot) append summaries to the PR body between marker
# comments. Those are the bot's words about the change, not the agent's claims.
_BOT_SECTION = re.compile(
    r"<!--\s*This is an auto-generated comment.*?-->.*?<!--\s*end of auto-generated comment.*?-->"
    r"|<!--\s*CURSOR_SUMMARY\s*-->.*?<!--\s*/CURSOR_SUMMARY\s*-->",
    re.DOTALL | re.IGNORECASE,
)


def without_blocks(text: str) -> str:
    """Drop bot sections, code blocks, HTML comments and quoted lines; keep inline code."""
    for pattern in (_BOT_SECTION, _FENCED_CODE, _HTML_COMMENT, _QUOTED_LINE):
        text = pattern.sub(" ", text)
    return text


def clean(text: str) -> str:
    """Drop everything that is not the agent's own prose: bot sections, code, HTML comments
    and quoted lines (quotes are someone else's words)."""
    return _INLINE_CODE.sub(" ", without_blocks(text))


# --------------------------------------------------------------------------------------------
# Step 2: clauses. Each line, sentence and checklist item is judged on its own.
# --------------------------------------------------------------------------------------------

_CHECKBOX = re.compile(r"^\s*(?:[-*+]|\d+[.)])?\s*\[( |x|X)\]\s*(.*)$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!;])\s+")
# Where one statement ends and an independent one starts inside a sentence, so a negation in
# the second part cannot cancel the first: "tests pass but integration tests had issues",
# "verified that tests pass and no other usages exist". Plain "and" is not split, because
# "tests and lint pass" is one statement about both.
# A relative clause after a comma describes something else ("fixes bug #12, where the plugin
# would misapply filters"), so it is judged on its own too.
_CLAUSE_BREAK = re.compile(
    r",?\s+but\s+|\s+and\s+(?=(?:no|not|none|nothing|never)\b)|,\s+(?=(?:where|which|when)\b)"
)


# A Markdown heading ("## Local testing") or a line that is only bold text, possibly as a list
# item ("**Testing:**", "1. **New Files**:").
_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s+(?P<hash>.+?)|(?:(?:[-*+]|\d+[.)])\s+)?\*\*(?P<bold>[^*]+)\*\*:?)\s*$"
)


def heading_of(line: str) -> str | None:
    """The heading text if ``line`` is a heading, else None."""
    found = _HEADING.match(line)
    return None if found is None else (found.group("hash") or found.group("bold")).strip()


def clauses(text: str) -> Iterator[str]:
    """Yield candidate clauses (see ``clauses_in_sections``)."""
    for clause, _heading in clauses_in_sections(text):
        yield clause


def clauses_in_sections(text: str) -> Iterator[tuple[str, str]]:
    """Yield (clause, heading of the section it is in). Unchecked checklist items are dropped
    here: ``- [ ] Tests pass`` is a to-do, not a claim."""
    heading = ""
    for line in text.splitlines():
        heading = heading_of(line) or heading
        box = _CHECKBOX.match(line)
        if box:
            if box.group(1) == " ":
                continue
            line = box.group(2)
        line = _BULLET.sub("", line).strip()
        for sentence in _SENTENCE_END.split(line):
            for part in _CLAUSE_BREAK.split(sentence):
                part = part.strip(" \t-*#:|")
                if part:
                    yield part, heading


# --------------------------------------------------------------------------------------------
# Step 3: drop clauses that are not plain assertions.
# --------------------------------------------------------------------------------------------

# Plans, hedges, quantifiers, conditions and negations. Any of these disqualifies the clause.
_NOT_AN_ASSERTION = re.compile(
    # plans and conditions
    r"\b(will|would|should|could|might|may|need to|needs to|todo|to do|not yet|once|unless|"
    r"until|hopefully|probably|expected to|supposed to|wip|pending|"
    # negations
    r"not|no|none|nothing|without|never|no longer|cannot|can't|"
    # failure words
    r"fail|fails|failing|failed|failure|broken|flaky|"
    # partial or quantified statements ("most tests pass" is not "tests pass")
    r"partially|partial|related|most|mostly|some|several|few|majority|except|excluding|"
    r"apart from|"
    r"where applicable)\b"
    r"|n't\b"
)
# A partial count ("19/22 tests passing") is a hedge, however positive the wording.
_COUNT = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")

# "No lint errors" or "compiles with no warnings" are positive claims even though they contain
# "no"; these exact phrases are removed before the negation test so claim patterns can still
# match them.
_POSITIVE_NO = re.compile(
    r"\bno\s+((lint|linting|linter|eslint|type)\s+)?(errors|warnings|issues)\b"
    r"|\bwithout\s+(any\s+)?(failures?|errors?|warnings?|issues)\b"
    # "211 pass, 0 fail", "zero warnings"
    r"|\b(0|zero)\s+(fail|fails|failed|failures?|errors?|warnings?|issues)\b"
)
# Quoted text names something ("the 'not re-exported' note"); its words are not the agent's
# own negation or hedge.
_QUOTED_TEXT = re.compile(r'"[^"\n]*"|“[^”\n]*”')


# "... to ensure / to verify X" says why something was done; X itself is not asserted.
_PURPOSE = re.compile(r"\bto\s+(?:ensure|guarantee|verify|confirm|make sure|check|validate)\b")


def asserted_part(clause: str) -> str:
    """The part of a clause before any purpose phrase ("ran tests to ensure X" → "ran tests")."""
    found = _PURPOSE.search(clause.lower())
    return clause if found is None else clause[: found.start()]


# A clause split off at ", which" describes the noun before it, often another PR or commit
# ("follows #12, which added schema tests"), not this change.
_RELATIVE_CLAUSE = re.compile(r"^\s*which\b", re.IGNORECASE)


# Conditions: "included tests if applicable", and template lines such as "If this pull request
# adds new functionality, it includes unit tests" (a rule, not a statement). Issue references
# are exempt (see is_assertion): "Fixes #42: the app crashes if the token expires" names the
# issue's subject, and the closing keyword itself is unconditional.
_CONDITION = re.compile(r"\b(if|whether)\b")


def is_assertion(clause: str, *, conditions_allowed: bool = False) -> bool:
    if clause.rstrip().endswith("?") or _RELATIVE_CLAUSE.match(clause):
        return False
    lowered = _POSITIVE_NO.sub(" ", _QUOTED_TEXT.sub(" ", clause.lower()))
    if any(int(done) < int(total) for done, total in _COUNT.findall(lowered)):
        return False
    if not conditions_allowed and _CONDITION.search(lowered):
        return False
    return not _NOT_AN_ASSERTION.search(lowered)


# --------------------------------------------------------------------------------------------
# Step 4: patterns per claim type (matched on the lower-cased clause).
# --------------------------------------------------------------------------------------------

_PASS_WORDS = r"(pass|passes|passed|passing|green|succeed|succeeds|succeeded|successful)"

# Every rule has a stable name. Claims record the rule that produced them, so precision can be
# measured per rule on the golden set (``second-source bench rules``) and only rules that hold
# the precision target are trusted (see TRUSTED_RULES below).
Rule = tuple[str, re.Pattern[str]]


def _rule(name: str, pattern: str) -> Rule:
    return name, re.compile(pattern)


_PATTERNS: dict[str, tuple[Rule, ...]] = {
    TESTS_PASS: (
        # "(?<!-)" keeps "self-test passed" (a script checking itself) out.
        _rule(
            "tests_pass.plain",
            r"\b(all\s+)?(the\s+)?(existing\s+|new\s+)?(unit\s+|integration\s+|e2e\s+)?"
            r"(?<!-)tests?\s+"
            rf"(are\s+|is\s+|now\s+|still\s+|all\s+)*{_PASS_WORDS}\b",
        ),
        # Quantified subjects with words in between: "All new and existing unit tests for the
        # filters plugin passed", "All 28 existing RWS tests continue passing".
        _rule(
            "tests_pass.quantified",
            r"\b(all|new|existing)\b[^.]{0,40}?(?<!-)\btests?\b[^.]{0,40}?"
            r"\b(passed|pass|passing|continue\s+(to\s+)?pass(ing)?)\b",
        ),
        # "7 tests added (all passing)", "27 tests, all passing".
        _rule(
            "tests_pass.all_passing",
            r"(?<!-)\btests?\b[^.]{0,40}\ball\s+(pass|passing|passed|green)\b",
        ),
        # "Verified compilation and tests", "verified with existing unit tests".
        _rule("tests_pass.verified", r"\bverified\b[^.]{0,40}?\b(tests?|test suite)\b"),
        _rule(
            "tests_pass.suite",
            rf"\b(test suite|test run|tests? run)\s+(is\s+)?(now\s+)?{_PASS_WORDS}\b",
        ),
        # CI as a whole. Bare "checks pass" is too vague ("repo checks pass" may be lint).
        _rule(
            "tests_pass.ci",
            r"\b(ci|all checks|all ci checks|ci checks)\s+(is\s+|are\s+)?(now\s+)?"
            r"(green|passing|pass|passed)\b",
        ),
        _rule(
            "tests_pass.runner_prose",
            r"\b(pytest|jest|vitest|npm test|yarn test|go test|cargo test|mocha|rspec)\b"
            r"[^.]{0,40}\b(pass|passes|passed|passing|green)\b",
        ),
        _rule(
            "tests_pass.tests_and_pass",
            r"\btests?\b[^.]{0,30}\band\s+(they\s+|these\s+|both\s+)?(are\s+|all\s+)*"
            r"(pass|passing|passed)\b",
        ),
        # "✅ Tests" or "✅ Tests pass" as a whole clause; not "✅ Tests added for X".
        _rule(
            "tests_pass.check_mark",
            r"✅\s*(all\s+)?tests?(\s+(pass|passes|passed|passing|green))?\s*[.!]?$",
        ),
    ),
    BUILD_OK: (
        _rule(
            "build_ok.plain",
            r"\b(the\s+)?build\s+(is\s+|now\s+)*"
            r"(passes|passed|passing|succeeds|succeeded|successful|green|ok|works)\b",
        ),
        _rule("build_ok.builds", r"\bbuilds\s+(successfully|cleanly|fine|without errors)\b"),
        _rule(
            "build_ok.compiles",
            r"\b(compiles|compiled)\s+(successfully|cleanly|fine|without (errors|warnings))\b",
        ),
        _rule(
            "build_ok.compilation", rf"\b(compilation|cargo check)\s+(is\s+|now\s+)*{_PASS_WORDS}\b"
        ),
        _rule(
            "build_ok.clean_or_verified",
            r"\b(clean|successful)\s+build\b|\bverified\b[^.]{0,30}?\bcompilation\b",
        ),
    ),
    LINT_OK: (
        _rule(
            "lint_ok.plain",
            r"\b(lint|linting|linter|linters|eslint|ruff|flake8|pylint|clippy)\s+"
            r"(checks?\s+)?(is\s+|are\s+|now\s+)*(passes|passed|passing|clean|green|happy|ok)\b",
        ),
        _rule(
            "lint_ok.no_errors",
            r"\bno\s+(lint|linting|linter|eslint|type)\s+(errors|warnings|issues)\b",
        ),
        # "Docs formatting, markdown lint, link audit, and repo checks pass".
        _rule(
            "lint_ok.lint_list",
            r"\b(lint|linting|linters?|formatting|format checks?)\b[^.]{0,50}?"
            r"\b(pass|passes|passed|passing|clean)\b",
        ),
        _rule(
            "lint_ok.verified", r"\bverified\s+(with|by|using)\s+(the\s+)?(lint|linting|linters?)\b"
        ),
        _rule(
            "lint_ok.type_check_tool", r"\b(type ?check(ing)?|mypy|tsc)\s+(passes|passed|clean)\b"
        ),
        _rule(
            "lint_ok.type_checks",
            rf"\b(typescript|type)\s+checks?\s+(are\s+|now\s+)*{_PASS_WORDS}\b",
        ),
        _rule(
            "lint_ok.zero_errors",
            r"\b(zero|0)\s+(lint|linter|linting)\s+(errors|warnings|issues)\b",
        ),
    ),
    TESTS_ADDED: (
        # "Added 3 new unit tests", "Added comprehensive unit tests", "Added a new E2E test".
        # Up to three describing words may sit between the verb and "tests", but not a
        # preposition: "added support for tests" is not adding tests.
        _rule(
            "tests_added.verb",
            r"\b(added|adds|add|wrote|writes|created|creates|includes|included|introduced|"
            r"introduces)\s+(?:(?!(?:for|to|of|in|with|on|from)\b)[\w-]+\s+){0,3}?"
            r"(tests?|test cases?|test coverage|specs?|assertions|regression coverage)\b"
            # "test metadata", "test scripts", "test data" are not new automated tests, and nor
            # are features or infrastructure named after testing: "added save and test
            # connection functionality", "created test PR #12", "add integration test workflow".
            r"(?!\s+(metadata|data|fixtures?|utilities|utils|helpers?|scripts?|ids?|names?|plans?|"
            r"connections?|prs?|pull requests?|workflows?|apps?|accounts?|users?|environments?|"
            r"servers?|mode|pages?|buttons?|endpoints?|commands?|targets?|jobs?|runners?)\b)",
        ),
        _rule(
            "tests_added.new_for",
            r"\bnew\s+(unit\s+|integration\s+|regression\s+)?tests?\s+(for|covering|to cover)\b",
        ),
        _rule(
            "tests_added.new_count", r"\bnew\W{1,6}\d+\s+(new\s+)?(unit\s+)?tests?\b"
        ),  # "**New**: 4 tests"
        _rule(
            "tests_added.coverage",
            r"\b(improved|increased|expanded|extended)\s+(\w+\s+)?test\s+coverage\b",
        ),
        _rule(
            "tests_added.extended_passive",
            r"\btests?\s+(were\s+|have been\s+)?(extended|expanded)\b",
        ),
        _rule("tests_added.added_passive", r"\btests?\s+(were\s+|have been\s+)?added\b"),
        # "11 tests covering all include patterns", "7 proxy integration tests added".
        _rule(
            "tests_added.count",
            r"\b\d+\s+(?:[\w-]+\s+){0,3}?(tests?|test cases)\s+(added|covering|to cover)\b",
        ),
        _rule(
            "tests_added.extended",
            r"\b(extended|expanded)\s+(the\s+)?(tests?|test suite|test coverage)\b",
        ),
        # Changed assertions check new behaviour; plain "updated tests" may be a refactor.
        _rule("tests_added.assertions", r"\bupdated\s+(the\s+)?test\s+assertions\b"),
    ),
    DOCS_UPDATED: (
        # "Updates the source-clickhouse user documentation", "Improves the Entity Cache
        # documentation": up to three describing words before the documentation noun.
        # "Updates the source-clickhouse user documentation", "update progress in TypeScript
        # README", "add a baseline doc": a change verb, then within five words a documentation
        # noun that is not docs tooling ("added logging to the documentation builder").
        _rule(
            "docs_updated.verb",
            r"\b(updated|updates|update|added|adds|add|wrote|write|improved|improves|improve|"
            r"corrected|corrects|correct|rewrote|rewrites|rewrite|expanded|expands|expand|"
            r"refreshed|refreshes|refresh|created|creates|create)\s+(?:[\w-]+\s+){0,5}?"
            r"(docs|documentation|readme|changelog|doc)\b"
            # Docs tooling, inline code docs ("XML doc comments" live in source files and are
            # not documentation files) and other nouns: "doc info", "docs extras".
            r"(?!\s+(builder|build|builds|generator|generation|site|tooling|pipeline|workflow|"
            r"comments?|strings?|blocks?|info|extras?|dependencies|deps|group)\b)",
        ),
        _rule("docs_updated.new", r"\bnew\s+(readme|docs|documentation|design doc|guide)\b"),
        # Documentation files named in plain text: "Update the instructions in AGENTS.md".
        _rule(
            "docs_updated.file",
            r"\b(updated|updates|update|added|adds|created|creates|edited|edits|rewrote|"
            r"rewrites|corrected|corrects|expanded|expands)\b[^.]{0,60}?"
            r"(?<![\w/.-])[\w/.-]+\.(md|mdx|rst|adoc)\b",
        ),
        # "README.md updated", "API docs updated", "documentation and examples updated".
        _rule(
            "docs_updated.passive",
            r"\b(docs|documentation|readme(\.md)?|changelog(\.md)?|news)\b[^.]{0,30}?"
            r"\b(are\s+|is\s+|was\s+|were\s+|have been\s+|has been\s+)?"
            r"(updated|added|improved|refreshed|expanded)\b",
        ),
    ),
}

# Matched separately because they capture values.
_ISSUE_REF = re.compile(
    r"\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s*:?\s+"
    r"(?:(?:the\s+)?(?:issue|bug)\s+)?"  # "fixes issue #12", "fixes bug #12"
    r"(?P<repo>[\w.-]+/[\w.-]+)?#(?P<number>\d+)\b"
)
_DEPLOY = re.compile(
    r"\b(?:deployed|deploys|shipped)\s+(?:to|on)\s+(?:the\s+)?"
    r"(?P<env>staging|production|prod|preview|dev|development|qa|test)\b"
)
_ENVIRONMENT_ALIASES = {"prod": "production", "dev": "development"}


# Phrases saying a check ran on the agent's own machine or sandbox rather than in CI.
# Matched on the clause and on the heading of its section ("## Local verification").
_LOCAL_SCOPE = re.compile(
    r"\blocally\b"
    r"|\blocal\s+(run|runs|testing|tests?|verification|validation|checks?|build|environment|"
    r"machine|setup)\b"
    r"|\b(on|in)\s+(my|the|a)\s+(local\s+)?(machine|sandbox|container|dev environment)\b"
    r"|\bsandbox\b"
)

# Claim types that can say where they ran, or that only part of the checks ran.
_SCOPED_TYPES = frozenset({TESTS_PASS, BUILD_OK, LINT_OK})

# A claim about part of the checks: "targeted tests pass", "unit tests for the importer pass",
# "the 12 new tests pass". CI can corroborate it, but an unrelated failing job (an E2E suite, a
# different package in a monorepo) does not make it false, so it gets ``scope=subset``.
# "Unit"/"integration"/"e2e" stay subsets even with "all": CI job names cannot be told apart
# reliably (a failing "test" job may be the E2E suite). A count or "new" is a subset unless the
# text says "all" ("All 120 tests pass") or "new and existing".
_SUBSET_SCOPE = re.compile(
    r"\b(targeted|relevant|affected|specific|selected|focused|unit|integration|e2e|"
    r"end-to-end|smoke)\s+(?:[\w-]+\s+){0,2}?tests?\b"
    r"|(?<!\ball\s)(?<!\band\s)(?<![\d,])\b(new|\d[\d,]*)\s+(?!and\s+existing\b)(?:[\w-]+\s+){0,2}?tests?\b"
    r"|\btests?\s+(for|of|under|covering)\b"
)
# Command arguments that select part of a suite: paths, test ids, filters, packages. Arguments
# that mean "everything" ("go test ./...", "--workspace", "--all") are not selectors.
_SELECTOR_ARGUMENT = re.compile(
    r"/|::|\.(py|[cm]?[jt]sx?|go|rs|rb|java|kt|cs)\b|^-(k|t|p)$|^--(filter|package|testnamepattern|"
    r"grep|project|scope)\b|^-[a-z]*k$"
)
_WHOLE_SUITE_ARGUMENT = re.compile(r"^(\./\.\.\.|\.\.\.|\.|\./|--workspace|--all|-r|--recursive)$")
# Script runners run the project's own lint script ("npm run lint"); a tool named directly
# ("biome lint", "ruff check .") is one linter among possibly several CI lint jobs.
_SCRIPT_RUNNERS = frozenset({"npm", "pnpm", "yarn", "bun", "make", "just", "task", "npx"})


def scope_of(clause: str, heading: str) -> str | None:
    """``local`` for checks run on the agent's machine, ``subset`` for part of the checks."""
    if is_local(clause, heading):
        return "local"
    if _SUBSET_SCOPE.search(clause.lower()):
        return "subset"
    return None


def command_scope(command: str) -> str | None:
    """``subset`` for a command that selects part of a suite or names one lint tool.

    "pytest tests/test_parser.py" checks one file; "biome lint" checks one linter. Either can
    pass while another CI job of the same kind fails.
    """
    words = command.lower().split()
    if not words:
        return None
    if is_lint_check(command) and words[0] not in _SCRIPT_RUNNERS:
        return "subset"
    arguments = [word for word in words[1:] if not _WHOLE_SUITE_ARGUMENT.match(word)]
    return "subset" if any(_SELECTOR_ARGUMENT.search(word) for word in arguments) else None


def is_local(*texts: str) -> bool:
    """True if any of the texts says the check ran locally (see ``_LOCAL_SCOPE``)."""
    return any(_LOCAL_SCOPE.search(text.lower()) for text in texts)


# Rules whose precision held the target on the golden set's selection split
# (trusted_rules.yaml, written by `second-source bench rules --select`). Claims they find are
# marked confidence "high"; all other claims "low". The benchmark headline counts only "high".
TRUSTED_RULES_FILE = Path(__file__).parent / "trusted_rules.yaml"
HIGH, LOW = "high", "low"


@lru_cache(maxsize=1)
def trusted_rules() -> frozenset[tuple[str, str]]:
    """The trusted (claim type, rule) pairs."""
    document = yaml.safe_load(TRUSTED_RULES_FILE.read_text(encoding="utf-8")) or {}
    return frozenset((entry["type"], entry["rule"]) for entry in document.get("trusted", []))


# Rule precision was measured on PR bodies only. Titles, commit messages and comments are a
# different genre ("test: add test app", "fix: add test"), so a rule's trust does not carry
# over: claims from those texts stay low confidence until they are labelled and measured.
MEASURED_ORIGINS = frozenset({"body"})


def confidence_of(claim_type: str, rule: str, origin: str = "body") -> str:
    trusted = origin in MEASURED_ORIGINS and (claim_type, rule) in trusted_rules()
    return HIGH if trusted else LOW


ISSUE_RULE = "fixes_issue.closing_ref"
DEPLOY_RULE = "deployed.phrase"
CODE_SENTENCE_RULES = (
    "command.marked",
    "command.prose",
    "changed_file.test",
    "changed_file.doc",
)


def rule_names() -> list[str]:
    """Every extraction rule name, in definition order."""
    clause_rules = [name for rules in _PATTERNS.values() for name, _pattern in rules]
    return [*clause_rules, ISSUE_RULE, DEPLOY_RULE, *CODE_SENTENCE_RULES]


def _match_clause(
    clause: str, scope: str | None, *, all_rules: bool = False
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (claim type, attributes) for every claim found in one clause.

    Normally one claim per type, from the first rule that matches. With ``all_rules`` every
    matching rule yields, which is what per-rule scoring needs.
    """
    lowered = clause.lower()

    for claim_type, rules in _PATTERNS.items():
        matched = [name for name, pattern in rules if pattern.search(lowered)]
        # Prefer a trusted rule when several match, so the claim gets the confidence it earned.
        matched.sort(key=lambda name: confidence_of(claim_type, name) != HIGH)
        for name in matched if all_rules else matched[:1]:
            attributes: dict[str, Any] = {"rule": name}
            if scope and claim_type in _SCOPED_TYPES:
                attributes["scope"] = scope
            yield claim_type, attributes

    for found in _ISSUE_REF.finditer(lowered):
        issue = int(found.group("number"))
        yield FIXES_ISSUE, {"issue": issue, "repo": found.group("repo") or "", "rule": ISSUE_RULE}

    for found in _DEPLOY.finditer(lowered):
        env = found.group("env")
        yield DEPLOYED, {"environment": _ENVIRONMENT_ALIASES.get(env, env), "rule": DEPLOY_RULE}


# --------------------------------------------------------------------------------------------
# Step 4b: sentences that name commands or files in inline code.
#
# `clean` removes inline code, so the clause patterns never see it. Real agents put the most
# checkable statements there, so these sentences are read separately:
#
# - **Command results.** "- ✅ `pytest -q`", "`pytest tests/`: 201 passed", "Ran `pnpm tsc`
#   and it completed successfully". A command claims success only when a ✅/✔ sits right
#   next to it, or the sentence's own prose says it passed. A ✅ used as a section emoji
#   ("**✅ Verification:** ran `npm test`") is not next to the command and does not count.
#   Sentences with ❌, ⚠️ or ✗ never claim success, and nor do purpose clauses ("ran
#   `npm test` to ensure it passes" says why it ran, not what happened).
# - **Changed files.** "Created `tests/test_parser.py`", "Updated `docs/setup.md`". A test
#   file that was added or extended is TESTS_ADDED; any edited documentation file is
#   DOCS_UPDATED. Paths listed under a "New files" or "Added" heading count as added.
# --------------------------------------------------------------------------------------------

_SUCCESS_MARK = re.compile(r"✅|✔")
_FAILURE_MARK = re.compile(r"❌|⚠|✗|✖|🚫")
_INLINE_COMMAND = re.compile(r"`([^`\n]+)`")
# A success mark directly before or after inline code (bold markers and separators allowed).
_MARKED_CODE = re.compile(
    r"(?:✅|✔)\s*(?:\*\*)?\s*`[^`\n]+`|`[^`\n]+`\s*(?:\*\*)?\s*[:\-–—]?\s*(?:✅|✔)"
)
# Prose saying a command succeeded ("201 passed" included).
_SUCCESS_PROSE = re.compile(
    r"\b(passed|passes|passing|succeeded|succeeds|successful|successfully|clean|green|"
    r"all pass|ok)\b|\b\d+\s*(/\s*\d+\s*)?(tests?\s+)?(passed|pass)\b"
    r"|\b(0|zero)\s+(errors|warnings|issues|failures)\b"
)

# Programs that run tests, builds or linters. Inline code only counts as a command if it
# starts with one of these; otherwise "✅ Created `tests/test_x.py`" would look like a test run.
_KNOWN_RUNNERS = frozenset(
    {
        "pytest", "py.test", "python", "python3", "uv", "poetry", "pipenv", "tox", "nox",
        "hatch", "npm", "npx", "yarn", "pnpm", "bun", "deno", "node", "jest", "vitest", "mocha",
        "go", "cargo", "make", "just", "mvn", "gradle", "./gradlew", "gradlew", "dotnet",
        "bundle", "rake", "rspec", "mix", "ruff", "eslint", "flake8", "pylint", "mypy", "black",
        "prettier", "tsc", "golangci-lint", "pre-commit", "bazel", "swift", "xcodebuild",
        "composer", "phpunit", "biome", "oxlint", "pyright", "rubocop", "stylelint",
    }
)  # fmt: skip

# Verbs that say a file was changed. Test files only count when added or extended (a test
# file "fixed" or "renamed" is not new testing); documentation counts for any edit.
_ADD_VERB = re.compile(
    r"\b(add|adds|added|create|creates|created|new|introduce|introduces|introduced|"
    r"extend|extends|extended|expand|expands|expanded|wrote|write|writes)\b"
)
_EDIT_VERB = re.compile(
    r"\b(add|adds|added|create|creates|created|new|update|updates|updated|modify|modifies|"
    r"modified|change|changes|changed|edit|edits|edited|fix|fixes|fixed|correct|corrects|"
    r"corrected|rewrite|rewrites|rewrote|document|documents|documented|expand|expands|"
    r"expanded|extend|extends|extended|improve|improves|improved|wrote|write|writes)\b"
)
# Paths named only as a reference ("as described in `README.md`") are not changes.
_REFERENCE_BEFORE = re.compile(
    r"\b(per|see|from|according to|described in|referenced in|following|like)\s*$"
)
_ADDED_SECTION = re.compile(r"\b(new|added|created)\b")


def _looks_like_command(code: str) -> bool:
    """True if inline code is a command line (``pytest -q``), not a file path or identifier."""
    tokens = code.strip().removeprefix("$").split()
    return bool(tokens) and tokens[0].lower() in _KNOWN_RUNNERS


def _looks_like_path(code: str) -> bool:
    """True if inline code is a file path (``docs/setup.md``, ``README.md``)."""
    code = code.strip()
    return " " not in code and bool(re.search(r"\.[a-z0-9]{1,6}$|/", code, re.IGNORECASE))


def _command_type(command: str) -> str | None:
    # Lint and build are tested first: "npm run test:lint" should count as lint, not tests.
    if is_lint_check(command):
        return LINT_OK
    if is_build_check(command):
        return BUILD_OK
    if is_test_check(command):
        return TESTS_PASS
    return None


def _changed_file_claims(sentence: str, prose: str, heading: str) -> Iterator[str]:
    """Claim types for files the sentence says were changed (rules ``changed_file.*``)."""
    in_added_section = bool(_ADDED_SECTION.search(heading.lower()))
    added = in_added_section or bool(_ADD_VERB.search(prose))
    edited = in_added_section or bool(_EDIT_VERB.search(prose))
    for found in _INLINE_COMMAND.finditer(sentence):
        path = found.group(1)
        if not _looks_like_path(path) or _REFERENCE_BEFORE.search(
            sentence[: found.start()].lower()
        ):
            continue
        if added and is_test_path(path):
            yield TESTS_ADDED
        elif edited and is_doc_path(path):
            yield DOCS_UPDATED


def _code_sentence_claims(text: str) -> Iterator[tuple[str, dict[str, Any], str]]:
    """Yield (claim type, attributes, span) from sentences that contain inline code."""
    heading = ""
    for line in without_blocks(text).splitlines():
        heading = heading_of(line) or heading
        box = _CHECKBOX.match(line)
        if box and box.group(1) == " ":
            continue  # unchecked to-do item
        for sentence in _SENTENCE_END.split(line):
            if "`" not in sentence or _FAILURE_MARK.search(sentence):
                continue
            prose = _INLINE_COMMAND.sub(" ", sentence).lower()
            if not is_assertion(prose):
                continue
            span = sentence.strip()
            local = is_local(sentence, heading)

            if _MARKED_CODE.search(sentence):
                rule = "command.marked"  # ✅ right next to the command
            elif _SUCCESS_PROSE.search(asserted_part(prose)):
                rule = "command.prose"  # "`pytest`: 201 passed"
            else:
                rule = None
            if rule:
                for command in filter(_looks_like_command, _INLINE_COMMAND.findall(sentence)):
                    claim_type = _command_type(command)
                    if claim_type:
                        scope = "local" if local else command_scope(command)
                        attributes = {"scope": scope} if scope else {}
                        yield claim_type, {**attributes, "rule": rule}, span

            for claim_type in _changed_file_claims(sentence, prose, heading):
                name = "changed_file.test" if claim_type == TESTS_ADDED else "changed_file.doc"
                yield claim_type, {"rule": name}, span


# --------------------------------------------------------------------------------------------
# Step 5: build Claim objects.
# --------------------------------------------------------------------------------------------

# Claim types whose verification depends on the commit the claim refers to.
_REF_TYPES = frozenset({TESTS_PASS, BUILD_OK, LINT_OK, DEPLOYED})


def _candidates(
    source: TextSource, *, all_rules: bool = False
) -> Iterator[tuple[str, dict[str, Any], str]]:
    """Every (claim type, attributes, span) found in one text source, in reading order."""
    for clause, heading in clauses_in_sections(clean(source.text)):
        if is_assertion(clause):
            scope = scope_of(clause, heading)
            matches = _match_clause(asserted_part(clause), scope, all_rules=all_rules)
            for claim_type, attributes in matches:
                yield claim_type, attributes, clause
        elif is_assertion(clause, conditions_allowed=True):
            # Only a condition word disqualified the clause: issue references still count.
            for claim_type, attributes in _match_clause(asserted_part(clause), None):
                if claim_type == FIXES_ISSUE:
                    yield claim_type, attributes, clause
    yield from _code_sentence_claims(source.text)


def rule_hits(text: str, origin: str = "body") -> set[tuple[str, str]]:
    """Every (claim type, rule name) that fires anywhere in ``text``.

    Unlike ``extract_claims`` this keeps all matching rules, not just the first per clause, so
    each rule can be scored on its own against a golden set.
    """
    source = TextSource(origin=origin, text=text, written_at=datetime.min)
    return {
        (kind, attributes["rule"]) for kind, attributes, _ in _candidates(source, all_rules=True)
    }


# Attributes that describe *how* something was said, not *what* was claimed. They are kept on
# the claim but ignored when deciding whether two claims are the same.
# "Tests pass" and "tests pass locally" are one claim; the first statement made is kept.
# "rule" records which extraction rule found the claim; it is not part of what was claimed.
_DESCRIPTIVE_ATTRIBUTES = frozenset({"scope", "rule", "confidence"})


def extract_claims(
    episode_id: str, sources: Sequence[TextSource], *, repo: str | None = None
) -> list[Claim]:
    """Extract claims from all text sources of one episode, in source order.

    Identical claims (same type, same substance, same commit) are kept once, using the
    earliest text that made them. ``repo`` is the PR's own repository (``owner/name``); issue
    references that name it explicitly are treated like plain ``#123`` references, so
    "Fixes #12" and "Fixes owner/name#12" are one claim.
    """
    claims: list[Claim] = []
    seen: dict[str, int] = {}  # claim key -> index in claims
    for source in sorted(sources, key=lambda s: s.written_at):
        for claim_type, attributes, span in _candidates(source):
            if claim_type in _REF_TYPES:
                attributes["ref"] = source.ref
            if claim_type in _SCOPED_TYPES and source.final_ref:
                attributes["final_ref"] = source.final_ref
            if claim_type == FIXES_ISSUE and repo and attributes["repo"] == repo.lower():
                attributes["repo"] = ""
            attributes["confidence"] = confidence_of(claim_type, attributes["rule"], source.origin)
            substance = {k: v for k, v in attributes.items() if k not in _DESCRIPTIVE_ATTRIBUTES}
            key = sha256_hex([episode_id, claim_type, substance])
            if key in seen:
                claims[seen[key]] = _merge_statement(claims[seen[key]], attributes, source, span)
                continue
            seen[key] = len(claims)
            claims.append(
                Claim(
                    id=f"clm_{key[:16]}",
                    episode_id=episode_id,
                    type=claim_type,
                    made_at=source.written_at,
                    attributes=attributes,
                    origin=source.origin,
                    span=span[:MAX_SPAN],
                )
            )
    return claims


# Narrower scopes protect a claim from contradiction; when statements disagree, the most
# protective one wins ("local" > "subset" > none).
_SCOPE_RANK = {"local": 2, "subset": 1}


def _merge_statement(
    kept: Claim, attributes: dict[str, Any], source: TextSource, span: str
) -> Claim:
    """Fold a repeated statement of the same claim into the claim already kept.

    - **Scope:** the most protective scope of all statements is kept. "Targeted tests pass" in
      the body must not lose its ``subset`` scope because a commit said "tests pass" first,
      and a ✅ `pnpm lint` command keeps its scope when prose in the same sentence also matched.
    - **Trust:** if this statement was found by a trusted rule and the kept one was not, the
      claim becomes high confidence, and the trusted statement (its span, origin and time)
      replaces the kept one, so a report quotes the text that earned the trust.
    """
    merged = dict(kept.attributes)
    scopes = [kept.attributes.get("scope"), attributes.get("scope")]
    scope = max(scopes, key=lambda value: _SCOPE_RANK.get(value or "", 0))
    if scope:
        merged["scope"] = scope
    if attributes["confidence"] == HIGH and kept.attributes["confidence"] == LOW:
        merged.update(rule=attributes["rule"], confidence=HIGH)
        return replace(
            kept,
            attributes=merged,
            origin=source.origin,
            made_at=source.written_at,
            span=span[:MAX_SPAN],
        )
    return replace(kept, attributes=merged)


def merge_claim(episode_id: str, merged_at: datetime, merge_sha: str) -> Claim:
    """The implicit claim every merged PR makes: "this change is good and will stay".

    Durability checks (reverts, reopened issues) verify this claim over time.
    """
    attributes = {"merge_sha": merge_sha}
    key = sha256_hex([episode_id, MERGE_HOLDS, attributes])
    return Claim(
        id=f"clm_{key[:16]}",
        episode_id=episode_id,
        type=MERGE_HOLDS,
        made_at=merged_at,
        attributes=attributes,
        origin="merge",
        span=None,
    )
