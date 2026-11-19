"""Find a GitHub token without asking the user to configure anything.

Order (first one found wins):

1. ``GITHUB_TOKEN`` (set automatically inside GitHub Actions) or ``GH_TOKEN``;
2. ``gh auth token``, if the GitHub CLI is installed and logged in;
3. no token: the public API still works, limited to 60 requests per hour.

The token value is never logged or printed; only *where* it came from is reported.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class TokenResult:
    token: str | None
    origin: str  # "GITHUB_TOKEN", "GH_TOKEN", "gh auth token" or "none"

    def __repr__(self) -> str:  # never reveal the token in logs or tracebacks
        return f"TokenResult(origin={self.origin!r}, token={'***' if self.token else None})"


def resolve_token(
    env: Mapping[str, str] | None = None, run: Runner = subprocess.run
) -> TokenResult:
    environment = os.environ if env is None else env
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = environment.get(name, "").strip()
        if value:
            return TokenResult(value, name)

    try:
        completed = run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        # gh is not installed or hung: fall through to unauthenticated access.
        return TokenResult(None, "none")
    token = completed.stdout.strip() if completed.returncode == 0 else ""
    return TokenResult(token, "gh auth token") if token else TokenResult(None, "none")
