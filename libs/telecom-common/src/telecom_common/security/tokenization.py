"""Deterministic, irreversible tokenization of direct identifiers (ADR-013).

Why HMAC rather than a plain hash
---------------------------------
The MSISDN space is small and structured: an operator prefix plus eight digits
is a few tens of millions of candidates. ``SHA256(msisdn)`` is therefore
brute-forceable on a laptop in minutes -- it is pseudonymisation in appearance
only. HMAC moves the salt into a *secret key*, which makes the search
infeasible without compromising that key.

Why deterministic
-----------------
Analytics needs to follow a subscriber across time and across sources
(``customer_360``, churn, fraud). A random token would make every join
impossible. The cost of determinism is correlation risk if the key leaks,
which is why the key is held only by the ingestion job and never by the
read path (Trino, API, agents).

Threat note
-----------
The key is the single most valuable asset in the platform (threat model, A1).
:class:`Tokenizer` therefore refuses weak keys and never exposes the key
through ``repr``, ``str`` or pickling -- the usual accidental-logging paths.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
from typing import Final

__all__ = [
    "MIN_KEY_BYTES",
    "TOKEN_BODY_LENGTH",
    "Tokenizer",
    "truncate_ip",
]

#: 256 bits. Shorter keys weaken the construction that the whole PII policy
#: rests on, so they are rejected rather than merely discouraged.
MIN_KEY_BYTES: Final = 32

#: Characters kept from the base64url digest. 32 chars = 192 bits of entropy,
#: far beyond what 12.4 M subscribers require, while keeping tokens readable
#: in logs and query results.
TOKEN_BODY_LENGTH: Final = 32


class Tokenizer:
    """Turns a direct identifier into a stable, irreversible token.

    The instance owns the secret key for its lifetime. Create one per process
    (in the Spark bronze -> silver job) rather than threading the raw key
    through call sites.

    Example:
        >>> t = Tokenizer(key=b"x" * 32)
        >>> token = t.tokenize("+22370000001")
        >>> token.startswith("v1:")
        True
        >>> t.tokenize("+22370000001") == token  # deterministic
        True
    """

    __slots__ = ("_key", "_version")

    def __init__(self, key: bytes, *, version: str = "v1") -> None:
        """Initialise the tokenizer.

        Args:
            key: secret HMAC key, at least :data:`MIN_KEY_BYTES` long.
            version: key version, embedded in every token so that a rotation
                can run as a dual-write migration instead of invalidating all
                historical tokens at once.

        Raises:
            ValueError: if the key is too short or the version is malformed.
        """
        if len(key) < MIN_KEY_BYTES:
            msg = (
                f"tokenization key must be at least {MIN_KEY_BYTES} bytes, "
                f"got {len(key)}; see ADR-013"
            )
            raise ValueError(msg)
        if not (version.startswith("v") and version[1:].isdigit()):
            msg = f"key version must look like 'v1', got {version!r}"
            raise ValueError(msg)
        self._key = key
        self._version = version

    @property
    def version(self) -> str:
        """Key version embedded in emitted tokens."""
        return self._version

    def tokenize(self, value: str) -> str:
        """Return the token for ``value``.

        Args:
            value: the direct identifier (MSISDN, IMSI, account number...).

        Raises:
            ValueError: if ``value`` is empty. An empty identifier is a data
                defect that must surface here rather than produce a token
                shared by every malformed record.
        """
        if not value:
            msg = "refusing to tokenize an empty value"
            raise ValueError(msg)
        digest = hmac.new(self._key, value.encode("utf-8"), hashlib.sha256).digest()
        body = base64.urlsafe_b64encode(digest).decode("ascii")[:TOKEN_BODY_LENGTH]
        return f"{self._version}:{body}"

    def tokenize_optional(self, value: str | None) -> str | None:
        """Tokenize ``value``, propagating ``None`` unchanged.

        Optional PII fields (``imei``, ``imsi``) are genuinely absent on some
        records; absence must stay absent rather than become a token.
        """
        return None if value is None else self.tokenize(value)

    def matches(self, value: str, token: str) -> bool:
        """Return whether ``value`` tokenizes to ``token``.

        Uses a constant-time comparison. This is the only sanctioned way to
        test a candidate against a known token -- it allows an investigator to
        confirm a suspicion without ever reversing the token.
        """
        return hmac.compare_digest(self.tokenize(value), token)

    # -- Accidental-disclosure guards ---------------------------------------
    # The key must never reach a log line, a traceback or a pickle. These are
    # the three paths by which secrets usually escape.

    def __repr__(self) -> str:
        return f"Tokenizer(version={self._version!r}, key=<redacted>)"

    __str__ = __repr__

    def __reduce__(self) -> tuple[object, ...]:
        msg = "Tokenizer is not serialisable: it holds a secret key"
        raise TypeError(msg)


def truncate_ip(address: str, *, prefix_v4: int = 24, prefix_v6: int = 48) -> str:
    """Generalise an IP address to its network prefix (PII policy, P2).

    A full address is an indirect identifier; the network prefix retains the
    analytical value (operator, rough locality) without identifying a session.

    Args:
        address: IPv4 or IPv6 address.
        prefix_v4: network bits kept for IPv4 (default ``/24``).
        prefix_v6: network bits kept for IPv6 (default ``/48``).

    Returns:
        The network in CIDR form, e.g. ``"10.2.3.4"`` -> ``"10.2.3.0/24"``.

    Raises:
        ValueError: if ``address`` is not a valid IP address.
    """
    parsed = ipaddress.ip_address(address)
    prefix = prefix_v4 if parsed.version == 4 else prefix_v6
    network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
    return str(network)
