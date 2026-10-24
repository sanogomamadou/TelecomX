"""Validated domain identifiers.

Every identifier that crosses a boundary is a constrained type rather than a
bare ``str``. The cost is a few lines here; the benefit is that a malformed
``cell_id`` fails at the edge of the system instead of travelling through
Kafka, bronze and silver to surface as an orphan row in a gold aggregate --
or, worse, as a confidently wrong number in an agent's answer.

Patterns are kept in sync with the ``telecomx.constraint`` attributes declared
in ``data-platform/schemas/avro/``.
"""

from __future__ import annotations

import re
from typing import Annotated, Final

from pydantic import StringConstraints

__all__ = [
    "ALARM_ID_PATTERN",
    "CELL_ID_PATTERN",
    "EQUIPMENT_ID_PATTERN",
    "SITE_ID_PATTERN",
    "TOKEN_PATTERN",
    "AlarmId",
    "CallId",
    "CellId",
    "EquipmentId",
    "IncidentId",
    "SiteId",
    "SubscriberToken",
    "TransactionId",
    "format_cell_id",
    "format_site_id",
    "is_valid_identifier",
]

# --- Patterns ---------------------------------------------------------------
# Anchored on both ends: an unanchored pattern would accept "XCELL-0421Y".

CELL_ID_PATTERN: Final = r"^CELL-\d{4}$"
SITE_ID_PATTERN: Final = r"^SITE-\d{3,4}$"
EQUIPMENT_ID_PATTERN: Final = r"^[A-Z][A-Z_]{1,19}-\d{3,4}$"
CALL_ID_PATTERN: Final = r"^CALL-\d{6,9}$"
ALARM_ID_PATTERN: Final = r"^ALM-\d{8}-\d{6}$"
TRANSACTION_ID_PATTERN: Final = r"^TRX-\d{8}-\d{8}$"
INCIDENT_ID_PATTERN: Final = r"^INC-\d{4}-\d{6}$"

#: Tokenized identifiers carry a key-version prefix so that the HMAC key can be
#: rotated without invalidating history (ADR-013, negative consequences).
# Why the suppression below: S105 flags any constant whose name contains
# "TOKEN" as a possible hardcoded credential. This is a validation regex, not
# a secret -- the only secret in the tokenization scheme is the HMAC key,
# which lives in the environment and never in source (NFR-SEC-007).
TOKEN_PATTERN: Final = r"^v\d+:[A-Za-z0-9_-]{32}$"  # noqa: S105


# --- Types ------------------------------------------------------------------

CellId = Annotated[str, StringConstraints(pattern=CELL_ID_PATTERN)]
"""Radio cell identifier, e.g. ``CELL-0421``."""

SiteId = Annotated[str, StringConstraints(pattern=SITE_ID_PATTERN)]
"""Physical site identifier, e.g. ``SITE-102``."""

EquipmentId = Annotated[str, StringConstraints(pattern=EQUIPMENT_ID_PATTERN)]
"""Infrastructure equipment identifier, e.g. ``ROUTER-082``, ``BBU-014``."""

CallId = Annotated[str, StringConstraints(pattern=CALL_ID_PATTERN)]
"""Core-network call identifier, e.g. ``CALL-928372``."""

AlarmId = Annotated[str, StringConstraints(pattern=ALARM_ID_PATTERN)]
"""Alarm occurrence identifier, e.g. ``ALM-20261024-000417``."""

TransactionId = Annotated[str, StringConstraints(pattern=TRANSACTION_ID_PATTERN)]
"""Mobile Money transaction identifier, e.g. ``TRX-20261024-00093812``."""

IncidentId = Annotated[str, StringConstraints(pattern=INCIDENT_ID_PATTERN)]
"""Incident identifier, e.g. ``INC-2026-009821``."""

SubscriberToken = Annotated[str, StringConstraints(pattern=TOKEN_PATTERN)]
"""Tokenized subscriber identifier. **Never** a MSISDN (ADR-013, NFR-SEC-001)."""

AccountToken = Annotated[str, StringConstraints(pattern=TOKEN_PATTERN)]
"""Tokenized Mobile Money account identifier."""

DeviceToken = Annotated[str, StringConstraints(pattern=TOKEN_PATTERN)]
"""Tokenized device identifier (derived from IMEI)."""


# --- Helpers ----------------------------------------------------------------

_COMPILED: Final[dict[str, re.Pattern[str]]] = {
    "cell": re.compile(CELL_ID_PATTERN),
    "site": re.compile(SITE_ID_PATTERN),
    "equipment": re.compile(EQUIPMENT_ID_PATTERN),
    "call": re.compile(CALL_ID_PATTERN),
    "alarm": re.compile(ALARM_ID_PATTERN),
    "transaction": re.compile(TRANSACTION_ID_PATTERN),
    "incident": re.compile(INCIDENT_ID_PATTERN),
    "token": re.compile(TOKEN_PATTERN),
}


def is_valid_identifier(kind: str, value: str) -> bool:
    """Return whether ``value`` matches the pattern for ``kind``.

    Args:
        kind: one of ``cell``, ``site``, ``equipment``, ``call``, ``alarm``,
            ``transaction``, ``incident``, ``token``.
        value: the candidate identifier.

    Raises:
        KeyError: if ``kind`` is unknown -- a typo in a caller must not
            silently degrade into "not valid".
    """
    return _COMPILED[kind].match(value) is not None


def format_cell_id(number: int) -> str:
    """Build a cell identifier from its number, e.g. ``421`` -> ``CELL-0421``.

    Raises:
        ValueError: if ``number`` does not fit the four-digit format.
    """
    if not 0 <= number <= 9999:
        msg = f"cell number must be in [0, 9999], got {number}"
        raise ValueError(msg)
    return f"CELL-{number:04d}"


def format_site_id(number: int) -> str:
    """Build a site identifier from its number, e.g. ``102`` -> ``SITE-102``.

    Raises:
        ValueError: if ``number`` does not fit the three-to-four digit format.
    """
    if not 0 <= number <= 9999:
        msg = f"site number must be in [0, 9999], got {number}"
        raise ValueError(msg)
    return f"SITE-{number:03d}"
