"""PII handling: tokenization at ingestion, detection at the output edge."""

from __future__ import annotations

from telecom_common.security.pii import (
    Confidence,
    PiiKind,
    PiiMatch,
    contains_pii,
    redact,
    scan,
)
from telecom_common.security.tokenization import (
    MIN_KEY_BYTES,
    TOKEN_BODY_LENGTH,
    Tokenizer,
    truncate_ip,
)

__all__ = [
    "MIN_KEY_BYTES",
    "TOKEN_BODY_LENGTH",
    "Confidence",
    "PiiKind",
    "PiiMatch",
    "Tokenizer",
    "contains_pii",
    "redact",
    "scan",
    "truncate_ip",
]
