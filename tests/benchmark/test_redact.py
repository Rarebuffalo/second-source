"""Tests for credential redaction in third-party text (benchmark/redact.py).

The key-like values below are fake: they have the right shape for the patterns but were never
issued by any provider.
"""

from second_source.benchmark.redact import redact_secrets

FAKE_ASIA = "ASIA" + "EXAMPLEFAKE00001"
FAKE_AKIA = "AKIA" + "EXAMPLEFAKE00002"


def test_presigned_s3_url_keeps_the_link_but_drops_the_credentials():
    url = (
        "https://bucket.s3.us-west-2.amazonaws.com/a.png?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        f"&X-Amz-Credential={FAKE_ASIA}%2F20250709%2Fus-west-2%2Fs3%2Faws4_request"
        "&X-Amz-Date=20250709T190226Z&X-Amz-Expires=604800"
        "&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEAQaCXVz&X-Amz-Signature=0a1b2c3d4e"
    )
    redacted = redact_secrets(f"![screenshot]({url})")
    assert FAKE_ASIA not in redacted
    assert "IQoJb3JpZ2luX2VjEAQaCXVz" not in redacted and "0a1b2c3d4e" not in redacted
    assert "X-Amz-Credential=REDACTED" in redacted
    assert "X-Amz-Date=20250709T190226Z" in redacted  # non-secret parameters stay
    assert redacted.startswith("![screenshot](https://bucket.s3.us-west-2.amazonaws.com/a.png?")
    assert redacted.endswith(")")


def test_bare_keys_and_tokens_keep_only_their_prefix():
    text = f"key {FAKE_AKIA} token ghp_{'x' * 36} slack xoxb-1234567890-abc"
    assert redact_secrets(text) == "key AKIAREDACTED token ghp_REDACTED slack xoxb-REDACTED"


def test_other_signed_url_schemes():
    text = "https://x.blob.core.windows.net/c/f?sv=2022&sig=abc%2Bdef&se=2026"
    assert (
        redact_secrets(text) == "https://x.blob.core.windows.net/c/f?sv=2022&sig=REDACTED&se=2026"
    )


def test_ordinary_text_is_unchanged():
    text = "All tests pass. Fixes #12. See https://example.com/a?design=sig&x=1 and task-based."
    assert redact_secrets(text) == text
