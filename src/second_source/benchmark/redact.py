"""Remove credentials from third-party text before we store or publish it.

PR descriptions in the AIDev dataset are public text written by agents, and some contain
credentials by accident. Example: Devin attaches screenshots through pre-signed S3 links,
whose query strings carry a temporary AWS key ID, a session token and a signature. Those
credentials are not ours and have long expired, but a repository should never redistribute
them, and secret scanners rightly flag them.

``redact_secrets`` replaces the secret part of each match with ``REDACTED`` and keeps the
surrounding text, so claim extraction and the golden-set labels are unaffected.
"""

from __future__ import annotations

import re

REDACTED = "REDACTED"

# Signed-URL query parameters (AWS SigV4, Google Cloud Storage V4, Azure SAS).
_SIGNED_URL_PARAM = re.compile(
    r"(?P<name>X-Amz-(?:Credential|Signature|Security-Token)"
    r"|X-Goog-(?:Credential|Signature)"
    r"|(?<=[?&])sig)="
    r"(?P<value>[^&\s\"'<>)\]]+)",
    re.IGNORECASE,
)

# Credential formats with a recognisable prefix. The prefix is kept so a reader can still see
# what kind of value was removed.
_TOKEN = re.compile(
    r"\b(?P<prefix>AKIA|ASIA)[0-9A-Z]{16}\b"  # AWS access key IDs (long-term, temporary)
    r"|\b(?P<gh>ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{20,}"  # GitHub tokens
    r"|\b(?P<slack>xox[abprs]-)[0-9A-Za-z-]{10,}"  # Slack tokens
)


def _token_replacement(match: re.Match[str]) -> str:
    prefix = match.group("prefix") or match.group("gh") or match.group("slack")
    return f"{prefix}{REDACTED}"


def redact_secrets(text: str) -> str:
    """Return ``text`` with signed-URL credentials and known token formats redacted."""
    text = _SIGNED_URL_PARAM.sub(lambda m: f"{m.group('name')}={REDACTED}", text)
    return _TOKEN.sub(_token_replacement, text)
