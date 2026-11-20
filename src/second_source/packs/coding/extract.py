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
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from second_source.core.hashing import sha256_hex
from second_source.core.model import Claim
from second_source.packs.coding.functions import is_build_check, is_lint_check, is_test_check

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


# --------------------------------------------------------------------------------------------
# Step 1: cleaning. Remove text that is not the agent's own statement.
# --------------------------------------------------------------------------------------------

_FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_QUOTED_LINE = re.compile(r"^\s*>.*$", re.MULTILINE)


def clean(text: str) -> str:
    """Drop code, HTML comments and quoted lines (quotes are someone else's words)."""
    for pattern in (_FENCED_CODE, _HTML_COMMENT, _QUOTED_LINE):
        text = pattern.sub(" ", text)
    return _INLINE_CODE.sub(" ", text)


# --------------------------------------------------------------------------------------------
# Step 2: clauses. Each line, sentence and checklist item is judged on its own.
# --------------------------------------------------------------------------------------------

_CHECKBOX = re.compile(r"^\s*(?:[-*+]|\d+[.)])?\s*\[( |x|X)\]\s*(.*)$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!;])\s+")


# A Markdown heading ("## Local testing") or a line that is only bold text ("**Testing:**").
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+(?P<hash>.+?)|\*\*(?P<bold>[^*]+)\*\*:?)\s*$")


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
        for part in _SENTENCE_END.split(line):
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
    r"apart from)\b"
    r"|n't\b"
)

# "No lint errors" or "compiles with no warnings" are positive claims even though they contain
# "no"; these exact phrases are removed before the negation test so claim patterns can still
# match them.
_POSITIVE_NO = re.compile(
    r"\bno\s+((lint|linting|linter|eslint|type)\s+)?(errors|warnings|issues)\b"
)


def is_assertion(clause: str) -> bool:
    if clause.rstrip().endswith("?"):
        return False
    lowered = _POSITIVE_NO.sub(" ", clause.lower())
    return not _NOT_AN_ASSERTION.search(lowered)


# --------------------------------------------------------------------------------------------
# Step 4: patterns per claim type (matched on the lower-cased clause).
# --------------------------------------------------------------------------------------------

_PASS_WORDS = r"(pass|passes|passed|passing|green|succeed|succeeds|succeeded|successful)"

_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    TESTS_PASS: (
        re.compile(
            r"\b(all\s+)?(the\s+)?(existing\s+|new\s+)?(unit\s+|integration\s+|e2e\s+)?tests?\s+"
            rf"(are\s+|is\s+|now\s+|still\s+|all\s+)*{_PASS_WORDS}\b"
        ),
        re.compile(rf"\b(test suite|test run|tests? run)\s+(is\s+)?(now\s+)?{_PASS_WORDS}\b"),
        re.compile(r"\b(ci|checks|all checks)\s+(is\s+|are\s+)?(now\s+)?(green|passing|pass)\b"),
        re.compile(
            r"\b(pytest|jest|vitest|npm test|yarn test|go test|cargo test|mocha|rspec)\b"
            r"[^.]{0,40}\b(pass|passes|passed|passing|green)\b"
        ),
        re.compile(
            r"\btests?\b[^.]{0,30}\band\s+(they\s+|these\s+|both\s+)?(are\s+|all\s+)*"
            r"(pass|passing|passed)\b"
        ),
        # "✅ Tests" or "✅ Tests pass" as a whole clause; not "✅ Tests added for X".
        re.compile(r"✅\s*(all\s+)?tests?(\s+(pass|passes|passed|passing|green))?\s*[.!]?$"),
    ),
    BUILD_OK: (
        re.compile(
            r"\b(the\s+)?build\s+(is\s+|now\s+)*"
            r"(passes|passed|passing|succeeds|succeeded|successful|green|ok|works)\b"
        ),
        re.compile(r"\bbuilds\s+(successfully|cleanly|fine|without errors)\b"),
        re.compile(
            r"\b(compiles|compiled)\s+(successfully|cleanly|fine|without (errors|warnings))\b"
        ),
    ),
    LINT_OK: (
        re.compile(
            r"\b(lint|linting|linter|linters|eslint|ruff|flake8|pylint|clippy)\s+"
            r"(checks?\s+)?(is\s+|are\s+|now\s+)*(passes|passed|passing|clean|green|happy|ok)\b"
        ),
        re.compile(r"\bno\s+(lint|linting|linter|eslint|type)\s+(errors|warnings|issues)\b"),
        re.compile(r"\b(type ?check(ing)?|mypy|tsc)\s+(passes|passed|clean)\b"),
    ),
    TESTS_ADDED: (
        re.compile(
            r"\b(added|adds|add|wrote|writes|created|creates|includes|included)\s+"
            r"(new\s+|more\s+|additional\s+)?(unit\s+|integration\s+|e2e\s+|regression\s+)?"
            r"(tests?|test cases?|test coverage|specs?)\b"
        ),
        re.compile(
            r"\bnew\s+(unit\s+|integration\s+|regression\s+)?tests?\s+(for|covering|to cover)\b"
        ),
        re.compile(r"\btests?\s+(were\s+|have been\s+)?added\b"),
    ),
    DOCS_UPDATED: (
        re.compile(
            r"\b(updated|updates|update|added|adds|wrote|improved|improves)\s+(the\s+)?"
            r"(docs|documentation|readme|changelog)\b"
        ),
        re.compile(
            r"\b(docs|documentation|readme|changelog)\s+"
            r"(are\s+|is\s+|was\s+|were\s+|have been\s+|has been\s+)?(updated|added)\b"
        ),
    ),
}

# Matched separately because they capture values.
_ISSUE_REF = re.compile(
    r"\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s*:?\s+"
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

# Claim types that can say where they ran.
_SCOPED_TYPES = frozenset({TESTS_PASS, BUILD_OK, LINT_OK})


def is_local(*texts: str) -> bool:
    """True if any of the texts says the check ran locally (see ``_LOCAL_SCOPE``)."""
    return any(_LOCAL_SCOPE.search(text.lower()) for text in texts)


def _match_clause(clause: str, local: bool) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (claim type, attributes) for every claim found in one clause."""
    lowered = clause.lower()

    for claim_type, patterns in _PATTERNS.items():
        if any(pattern.search(lowered) for pattern in patterns):
            yield claim_type, ({"scope": "local"} if local and claim_type in _SCOPED_TYPES else {})

    for found in _ISSUE_REF.finditer(lowered):
        yield FIXES_ISSUE, {"issue": int(found.group("number")), "repo": found.group("repo") or ""}

    for found in _DEPLOY.finditer(lowered):
        env = found.group("env")
        yield DEPLOYED, {"environment": _ENVIRONMENT_ALIASES.get(env, env)}


# --------------------------------------------------------------------------------------------
# Step 4b: command status lines, e.g. "- ✅ `pytest -q`".
#
# Some agents (notably Codex) report checks as a list of commands with a status emoji. The
# command is inline code, which `clean` removes, so these lines are read separately from text
# that still contains inline code. A ✅/✔ next to a command claims it succeeded; lines with
# ❌, ⚠️ or ✗ are failure or warning reports and never produce a claim.
# --------------------------------------------------------------------------------------------

_SUCCESS_MARK = re.compile(r"✅|✔")
_FAILURE_MARK = re.compile(r"❌|⚠|✗|✖|🚫")
_INLINE_COMMAND = re.compile(r"`([^`\n]+)`")

# Programs that run tests, builds or linters. Inline code only counts as a command if it
# starts with one of these; otherwise "✅ Created `tests/test_x.py`" would look like a test run.
_KNOWN_RUNNERS = frozenset(
    {
        "pytest", "py.test", "python", "python3", "uv", "poetry", "pipenv", "tox", "nox",
        "hatch", "npm", "npx", "yarn", "pnpm", "bun", "deno", "node", "jest", "vitest", "mocha",
        "go", "cargo", "make", "just", "mvn", "gradle", "./gradlew", "gradlew", "dotnet",
        "bundle", "rake", "rspec", "mix", "ruff", "eslint", "flake8", "pylint", "mypy", "black",
        "prettier", "tsc", "golangci-lint", "pre-commit", "bazel", "swift", "xcodebuild",
        "composer", "phpunit",
    }
)  # fmt: skip


def _looks_like_command(code: str) -> bool:
    """True if inline code is a command line (``pytest -q``), not a file path or identifier."""
    tokens = code.strip().removeprefix("$").split()
    return bool(tokens) and tokens[0].lower() in _KNOWN_RUNNERS


def _command_claims(text: str) -> Iterator[tuple[str, dict[str, Any], str]]:
    """Yield (claim type, attributes, span) for each successful command status line."""
    without_blocks = _QUOTED_LINE.sub(" ", _HTML_COMMENT.sub(" ", _FENCED_CODE.sub(" ", text)))
    heading = ""
    for line in without_blocks.splitlines():
        heading = heading_of(line) or heading
        if not _SUCCESS_MARK.search(line) or _FAILURE_MARK.search(line):
            continue
        # The prose around the command must itself be a plain assertion
        # ("✅ `pytest` (not run: missing deps)" is not).
        if not is_assertion(_INLINE_COMMAND.sub(" ", line)):
            continue
        attributes = {"scope": "local"} if is_local(line, heading) else {}
        for command in filter(_looks_like_command, _INLINE_COMMAND.findall(line)):
            # Lint and build are tested first: "npm run lint" also contains no test word,
            # but "npm run test:lint" should count as lint, not tests.
            if is_lint_check(command):
                yield LINT_OK, dict(attributes), line.strip()
            elif is_build_check(command):
                yield BUILD_OK, dict(attributes), line.strip()
            elif is_test_check(command):
                yield TESTS_PASS, dict(attributes), line.strip()


# --------------------------------------------------------------------------------------------
# Step 5: build Claim objects.
# --------------------------------------------------------------------------------------------

# Claim types whose verification depends on the commit the claim refers to.
_REF_TYPES = frozenset({TESTS_PASS, BUILD_OK, LINT_OK, DEPLOYED})


def _candidates(source: TextSource) -> Iterator[tuple[str, dict[str, Any], str]]:
    """Every (claim type, attributes, span) found in one text source, in reading order."""
    for clause, heading in clauses_in_sections(clean(source.text)):
        if is_assertion(clause):
            for claim_type, attributes in _match_clause(clause, is_local(clause, heading)):
                yield claim_type, attributes, clause
    yield from _command_claims(source.text)


# Attributes that describe *how* something was said, not *what* was claimed. They are kept on
# the claim but ignored when deciding whether two claims are the same.
# "Tests pass" and "tests pass locally" are one claim; the first statement made is kept.
_DESCRIPTIVE_ATTRIBUTES = frozenset({"scope"})


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
    seen: set[str] = set()
    for source in sorted(sources, key=lambda s: s.written_at):
        for claim_type, attributes, span in _candidates(source):
            if claim_type in _REF_TYPES:
                attributes["ref"] = source.ref
            if claim_type == FIXES_ISSUE and repo and attributes["repo"] == repo.lower():
                attributes["repo"] = ""
            substance = {k: v for k, v in attributes.items() if k not in _DESCRIPTIVE_ATTRIBUTES}
            key = sha256_hex([episode_id, claim_type, substance])
            if key in seen:
                continue
            seen.add(key)
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
