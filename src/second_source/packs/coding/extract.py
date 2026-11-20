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
# Where one statement ends and an independent one starts inside a sentence, so a negation in
# the second part cannot cancel the first: "tests pass but integration tests had issues",
# "verified that tests pass and no other usages exist". Plain "and" is not split, because
# "tests and lint pass" is one statement about both.
_CLAUSE_BREAK = re.compile(r",?\s+but\s+|\s+and\s+(?=(?:no|not|none|nothing|never)\b)")


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
    r"apart from)\b"
    r"|n't\b"
)

# "No lint errors" or "compiles with no warnings" are positive claims even though they contain
# "no"; these exact phrases are removed before the negation test so claim patterns can still
# match them.
_POSITIVE_NO = re.compile(
    r"\bno\s+((lint|linting|linter|eslint|type)\s+)?(errors|warnings|issues)\b"
    r"|\bwithout\s+(any\s+)?(failures?|errors?|warnings?|issues)\b"
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


def is_assertion(clause: str) -> bool:
    if clause.rstrip().endswith("?"):
        return False
    lowered = _POSITIVE_NO.sub(" ", _QUOTED_TEXT.sub(" ", clause.lower()))
    return not _NOT_AN_ASSERTION.search(lowered)


# --------------------------------------------------------------------------------------------
# Step 4: patterns per claim type (matched on the lower-cased clause).
# --------------------------------------------------------------------------------------------

_PASS_WORDS = r"(pass|passes|passed|passing|green|succeed|succeeds|succeeded|successful)"

_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    TESTS_PASS: (
        # "(?<!-)" keeps "self-test passed" (a script checking itself) out.
        re.compile(
            r"\b(all\s+)?(the\s+)?(existing\s+|new\s+)?(unit\s+|integration\s+|e2e\s+)?"
            r"(?<!-)tests?\s+"
            rf"(are\s+|is\s+|now\s+|still\s+|all\s+)*{_PASS_WORDS}\b"
        ),
        # Quantified subjects with words in between: "All new and existing unit tests for the
        # filters plugin passed", "All 28 existing RWS tests continue passing".
        re.compile(
            r"\b(all|new|existing)\b[^.]{0,40}?(?<!-)\btests?\b[^.]{0,40}?"
            r"\b(passed|pass|passing|continue\s+(to\s+)?pass(ing)?)\b"
        ),
        # "7 tests added (all passing)".
        re.compile(r"(?<!-)\btests?\b[^.]{0,40}\(\s*all\s+(pass|passing|passed|green)\s*\)"),
        re.compile(rf"\b(test suite|test run|tests? run)\s+(is\s+)?(now\s+)?{_PASS_WORDS}\b"),
        # CI as a whole. Bare "checks pass" is too vague ("repo checks pass" may be lint).
        re.compile(
            r"\b(ci|all checks|all ci checks|ci checks)\s+(is\s+|are\s+)?(now\s+)?"
            r"(green|passing|pass|passed)\b"
        ),
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
        # "Docs formatting, markdown lint, link audit, and repo checks pass".
        re.compile(
            r"\b(lint|linting|linters?|formatting|format checks?)\b[^.]{0,50}?"
            r"\b(pass|passes|passed|passing|clean)\b"
        ),
        re.compile(r"\bverified\s+(with|by|using)\s+(the\s+)?(lint|linting|linters?)\b"),
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
    r"all pass|ok)\b|\b\d+\s*(/\s*\d+\s*)?(tests?\s+)?passed\b"
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
        "composer", "phpunit",
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
    """Claim types for files the sentence says were changed."""
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
    without_blocks = _QUOTED_LINE.sub(" ", _HTML_COMMENT.sub(" ", _FENCED_CODE.sub(" ", text)))
    heading = ""
    for line in without_blocks.splitlines():
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
            attributes = {"scope": "local"} if is_local(sentence, heading) else {}

            succeeded = bool(_MARKED_CODE.search(sentence)) or bool(
                _SUCCESS_PROSE.search(asserted_part(prose))
            )
            if succeeded:
                for command in filter(_looks_like_command, _INLINE_COMMAND.findall(sentence)):
                    claim_type = _command_type(command)
                    if claim_type:
                        yield claim_type, dict(attributes), span

            for claim_type in _changed_file_claims(sentence, prose, heading):
                yield claim_type, {}, span


# --------------------------------------------------------------------------------------------
# Step 5: build Claim objects.
# --------------------------------------------------------------------------------------------

# Claim types whose verification depends on the commit the claim refers to.
_REF_TYPES = frozenset({TESTS_PASS, BUILD_OK, LINT_OK, DEPLOYED})


def _candidates(source: TextSource) -> Iterator[tuple[str, dict[str, Any], str]]:
    """Every (claim type, attributes, span) found in one text source, in reading order."""
    for clause, heading in clauses_in_sections(clean(source.text)):
        if is_assertion(clause):
            local = is_local(clause, heading)
            for claim_type, attributes in _match_clause(asserted_part(clause), local):
                yield claim_type, attributes, clause
    yield from _code_sentence_claims(source.text)


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
