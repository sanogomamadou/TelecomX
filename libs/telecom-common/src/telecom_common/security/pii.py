"""Detection and redaction of personal identifiers in free text.

Used in three places, all of them last lines of defence rather than primary
controls:

1. CI assertions that no MSISDN reached silver or gold (NFR-SEC-001);
2. output filtering on agent answers (NFR-SEC-010);
3. the OpenTelemetry span processor that strips attributes before export.

Design note
-----------
Detection is *not* the mechanism that protects PII -- tokenization at ingestion
is (ADR-013). A regex scan over free text will always be approximate. Its job
is to catch what escaped the structural guarantee, which is why matches carry
a confidence level: callers that must not produce false negatives (CI tests)
act on everything, while callers that must not produce false positives
(redacting a user-facing answer) act on high confidence only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "Confidence",
    "PiiKind",
    "PiiMatch",
    "contains_pii",
    "redact",
    "scan",
]


class PiiKind(StrEnum):
    """Category of personal identifier."""

    MSISDN = "msisdn"
    IMSI = "imsi"
    IMEI = "imei"
    EMAIL = "email"
    COORDINATES = "coordinates"


class Confidence(StrEnum):
    """How certain a match is.

    ``HIGH`` matches carry an unambiguous marker (a ``+223`` prefix, an ``@``).
    ``MEDIUM`` matches are shape-based and can collide with legitimate values --
    an eight-digit amount in XOF looks exactly like a local MSISDN.
    """

    HIGH = "high"
    MEDIUM = "medium"


@dataclass(frozen=True, slots=True)
class PiiMatch:
    """A single detection within a scanned string."""

    kind: PiiKind
    value: str
    start: int
    end: int
    confidence: Confidence


# --- Patterns ---------------------------------------------------------------
# Mali MSISDNs are +223 followed by 8 digits. The international form is
# unambiguous; the bare 8-digit form is not, hence the confidence split.

_PATTERNS: Final[tuple[tuple[PiiKind, re.Pattern[str], Confidence], ...]] = (
    (
        PiiKind.MSISDN,
        # The trailing digit is matched separately: repeating "digit +
        # optional separator" eight times would let the last repetition
        # swallow the space *after* the number, so redaction would eat the
        # following word boundary.
        re.compile(r"(?<![\d+])\+?223[\s.-]?(?:\d[\s.-]?){7}\d(?!\d)"),
        Confidence.HIGH,
    ),
    (
        PiiKind.EMAIL,
        re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
        Confidence.HIGH,
    ),
    (
        PiiKind.IMSI,
        # Mobile Country Code 610 = Mali, followed by MNC and MSIN (15 digits total).
        re.compile(r"(?<!\d)610\d{12}(?!\d)"),
        Confidence.HIGH,
    ),
    (
        PiiKind.IMEI,
        re.compile(r"(?<!\d)\d{15}(?!\d)"),
        Confidence.MEDIUM,
    ),
    (
        PiiKind.MSISDN,
        # Bare local form: 8 digits starting with a mobile prefix (6, 7, 8, 9).
        # Collides with amounts, which is why it is MEDIUM.
        re.compile(r"(?<![\d.,+-])[6-9]\d{7}(?![\d.,])"),
        Confidence.MEDIUM,
    ),
    (
        PiiKind.COORDINATES,
        # Decimal lat/lon pair -- precise location is P2 and must be generalised.
        re.compile(r"(?<!\d)-?\d{1,2}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}(?!\d)"),
        Confidence.MEDIUM,
    ),
)


def scan(text: str, *, min_confidence: Confidence | None = None) -> list[PiiMatch]:
    """Return every personal identifier found in ``text``.

    Args:
        text: the string to inspect.
        min_confidence: when ``Confidence.HIGH``, keep only unambiguous matches.
            Leave as ``None`` to return everything -- the right choice for CI
            assertions, where a false positive costs a review and a false
            negative costs a leak.

    Returns:
        Matches ordered by position. Overlapping matches are resolved in favour
        of the first (highest-priority) pattern that claimed the span.
    """
    claimed: list[PiiMatch] = []
    for kind, pattern, confidence in _PATTERNS:
        if min_confidence is Confidence.HIGH and confidence is not Confidence.HIGH:
            continue
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < m.end and m.start < end for m in claimed):
                continue  # already covered by a higher-priority pattern
            claimed.append(
                PiiMatch(
                    kind=kind,
                    value=match.group(),
                    start=start,
                    end=end,
                    confidence=confidence,
                )
            )
    return sorted(claimed, key=lambda m: m.start)


def contains_pii(text: str, *, min_confidence: Confidence | None = None) -> bool:
    """Return whether ``text`` contains any personal identifier."""
    return bool(scan(text, min_confidence=min_confidence))


def redact(text: str, *, min_confidence: Confidence | None = None) -> str:
    """Replace every detected identifier with a typed placeholder.

    The placeholder keeps the *kind* visible so that a redacted log line stays
    diagnosable: ``"call from [MSISDN] failed"`` is useful, ``"call from ***
    failed"`` is not.
    """
    result: list[str] = []
    cursor = 0
    for match in scan(text, min_confidence=min_confidence):
        result.append(text[cursor : match.start])
        result.append(f"[{match.kind.value.upper()}]")
        cursor = match.end
    result.append(text[cursor:])
    return "".join(result)
