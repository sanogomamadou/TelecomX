"""Tests for PII tokenization (ADR-013).

These tests guard the property the whole PII policy rests on: a token must be
stable enough to join on, and useless to anyone without the key.
"""

from __future__ import annotations

import pickle
import re

import pytest

from telecom_common.security.tokenization import (
    MIN_KEY_BYTES,
    TOKEN_BODY_LENGTH,
    Tokenizer,
    truncate_ip,
)

pytestmark = pytest.mark.unit

KEY = b"0123456789abcdef0123456789abcdef"  # exactly MIN_KEY_BYTES
OTHER_KEY = b"fedcba9876543210fedcba9876543210"
MSISDN = "+22370123456"


@pytest.fixture
def tokenizer() -> Tokenizer:
    return Tokenizer(key=KEY)


class TestKeyValidation:
    def test_accepts_minimum_length_key(self) -> None:
        assert Tokenizer(key=b"x" * MIN_KEY_BYTES).version == "v1"

    def test_rejects_short_key(self) -> None:
        """A weak key breaks the only thing standing between tokens and identities."""
        with pytest.raises(ValueError, match="at least 32 bytes"):
            Tokenizer(key=b"too-short")

    def test_rejects_malformed_version(self) -> None:
        with pytest.raises(ValueError, match="key version"):
            Tokenizer(key=KEY, version="version-one")


class TestTokenize:
    def test_is_deterministic(self, tokenizer: Tokenizer) -> None:
        """Without determinism there is no customer_360, no churn, no fraud."""
        assert tokenizer.tokenize(MSISDN) == tokenizer.tokenize(MSISDN)

    def test_distinct_inputs_give_distinct_tokens(self, tokenizer: Tokenizer) -> None:
        assert tokenizer.tokenize("+22370123456") != tokenizer.tokenize("+22370123457")

    def test_token_has_version_prefix(self, tokenizer: Tokenizer) -> None:
        """The prefix is what makes key rotation a migration rather than a reset."""
        assert tokenizer.tokenize(MSISDN).startswith("v1:")

    def test_token_shape_matches_identifier_pattern(self, tokenizer: Tokenizer) -> None:
        token = tokenizer.tokenize(MSISDN)
        assert re.fullmatch(rf"v\d+:[A-Za-z0-9_-]{{{TOKEN_BODY_LENGTH}}}", token)

    def test_token_is_url_safe(self, tokenizer: Tokenizer) -> None:
        """Tokens travel in URLs, partition paths and SQL literals."""
        body = tokenizer.tokenize(MSISDN).split(":", 1)[1]
        assert "+" not in body
        assert "/" not in body
        assert "=" not in body

    def test_token_does_not_leak_the_input(self, tokenizer: Tokenizer) -> None:
        token = tokenizer.tokenize(MSISDN)
        assert MSISDN not in token
        assert "70123456" not in token

    def test_different_keys_give_different_tokens(self) -> None:
        """A leaked token from one environment is useless against another."""
        assert Tokenizer(key=KEY).tokenize(MSISDN) != Tokenizer(key=OTHER_KEY).tokenize(MSISDN)

    def test_different_versions_give_different_tokens(self) -> None:
        a = Tokenizer(key=KEY, version="v1").tokenize(MSISDN)
        b = Tokenizer(key=KEY, version="v2").tokenize(MSISDN)
        assert a != b

    def test_rejects_empty_value(self, tokenizer: Tokenizer) -> None:
        """Otherwise every malformed record would share one token."""
        with pytest.raises(ValueError, match="empty value"):
            tokenizer.tokenize("")

    def test_no_collision_across_a_realistic_subscriber_range(self, tokenizer: Tokenizer) -> None:
        tokens = {tokenizer.tokenize(f"+2237{n:07d}") for n in range(5_000)}
        assert len(tokens) == 5_000


class TestTokenizeOptional:
    def test_propagates_none(self, tokenizer: Tokenizer) -> None:
        """Absent optional PII must stay absent, not become a token."""
        assert tokenizer.tokenize_optional(None) is None

    def test_tokenizes_value(self, tokenizer: Tokenizer) -> None:
        assert tokenizer.tokenize_optional(MSISDN) == tokenizer.tokenize(MSISDN)


class TestMatches:
    def test_confirms_correct_candidate(self, tokenizer: Tokenizer) -> None:
        assert tokenizer.matches(MSISDN, tokenizer.tokenize(MSISDN))

    def test_rejects_wrong_candidate(self, tokenizer: Tokenizer) -> None:
        assert not tokenizer.matches("+22370000000", tokenizer.tokenize(MSISDN))


class TestSecretProtection:
    """The key must not escape through logging, tracebacks or serialisation."""

    def test_repr_redacts_key(self, tokenizer: Tokenizer) -> None:
        assert "redacted" in repr(tokenizer)
        assert "0123456789abcdef" not in repr(tokenizer)

    def test_str_redacts_key(self, tokenizer: Tokenizer) -> None:
        assert "0123456789abcdef" not in str(tokenizer)

    def test_cannot_be_pickled(self, tokenizer: Tokenizer) -> None:
        """Pickling would write the key to disk or send it over the wire."""
        with pytest.raises(TypeError, match="secret key"):
            pickle.dumps(tokenizer)

    def test_has_no_public_key_attribute(self, tokenizer: Tokenizer) -> None:
        assert not hasattr(tokenizer, "key")


class TestTruncateIp:
    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("10.2.3.4", "10.2.3.0/24"),
            ("192.168.1.254", "192.168.1.0/24"),
            ("41.221.0.57", "41.221.0.0/24"),
        ],
    )
    def test_truncates_ipv4_to_slash_24(self, address: str, expected: str) -> None:
        assert truncate_ip(address) == expected

    def test_truncates_ipv6(self) -> None:
        assert truncate_ip("2001:db8:abcd:1234::1") == "2001:db8:abcd::/48"

    def test_honours_custom_prefix(self) -> None:
        assert truncate_ip("10.2.3.4", prefix_v4=16) == "10.2.0.0/16"

    def test_rejects_invalid_address(self) -> None:
        with pytest.raises(ValueError, match="does not appear to be"):
            truncate_ip("not-an-ip")
