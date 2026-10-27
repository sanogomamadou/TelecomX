"""Cross-cutting domain enumerations.

Scope rule: this module holds only enums that are **not** owned by a data
contract -- platform concepts (risk classes, lake zones, PII classes) and
reference-data dimensions (technology, region).

Enums that belong to an event schema (``CallStatus``, ``AlarmSeverity``,
``TransactionType``, ...) are **generated** from ``data-platform/schemas/avro/``
and must not be hand-written here. Duplicating them would recreate exactly the
divergence ADR-015 exists to prevent.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AreaType",
    "BackhaulType",
    "DataZone",
    "PiiClass",
    "Region",
    "RiskClass",
    "SiteType",
    "Technology",
    "TenantId",
]


class AreaType(StrEnum):
    """Dominant character of a region (reference data, ``ref.regions``)."""

    URBAN = "urban"
    PERIURBAN = "periurban"
    RURAL = "rural"


class SiteType(StrEnum):
    """Kind of physical site. Drives capacity, power and equipment layout."""

    MACRO = "macro"
    MICRO = "micro"
    INDOOR = "indoor"
    ROOFTOP = "rooftop"


class BackhaulType(StrEnum):
    """Transport link between a site and the core network.

    Its capacity is what makes a *backhaul congestion* diagnosis possible: a link
    smaller than the sum of its cells' peaks can saturate under load.
    """

    FIBER = "fiber"
    MICROWAVE = "microwave"
    SATELLITE = "satellite"


class Technology(StrEnum):
    """Radio access technology.

    The ``G`` prefix is not cosmetic: Avro enum symbols must be valid names and
    cannot start with a digit, so the wire format uses ``G4`` where the business
    says "4G". :attr:`label` converts back for display.
    """

    G2 = "G2"
    G3 = "G3"
    G4 = "G4"
    G5 = "G5"

    @property
    def label(self) -> str:
        """Human-facing name, e.g. ``G4`` -> ``4G``."""
        return f"{self.value[1:]}G"

    @property
    def generation(self) -> int:
        """Numeric generation, e.g. ``G4`` -> ``4``. Useful for ordering."""
        return int(self.value[1:])


class Region(StrEnum):
    """Administrative regions covered by the (fictitious) operator.

    Codes match ``ref.regions`` and are used as a partition dimension on silver
    tables (see docs/architecture/02-data-architecture.md).
    """

    BAMAKO = "BKO"
    KAYES = "KYS"
    KOULIKORO = "KLK"
    SIKASSO = "SKS"
    SEGOU = "SGU"
    MOPTI = "MPT"
    TOMBOUCTOU = "TBT"
    GAO = "GAO"
    KIDAL = "KDL"

    @property
    def label(self) -> str:
        """Display name, e.g. ``TBT`` -> ``Tombouctou``."""
        return self.name.capitalize()


class DataZone(StrEnum):
    """Lakehouse refinement zone.

    Ordering matters: data only ever flows bronze -> silver -> gold. Bronze is
    immutable (principle P2); a correction happens downstream, never by
    rewriting the raw zone.
    """

    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"


class PiiClass(StrEnum):
    """Sensitivity class of a field, mirroring ``telecomx.pii`` in the contracts.

    Drives automatic tokenization in the bronze -> silver job (ADR-013). A field
    declared ``DIRECT`` or ``INDIRECT`` that is not tokenized fails the build.
    """

    NONE = "none"
    DIRECT = "direct"
    INDIRECT = "indirect"

    @property
    def requires_tokenization(self) -> bool:
        """Whether a field of this class must never reach silver in the clear."""
        return self is not PiiClass.NONE


class RiskClass(StrEnum):
    """Risk class of an agent tool, governing its approval regime (PRD section 7).

    This is the backbone of the autonomy model. The class is declared on the
    tool itself and enforced at three levels: catalogue filtering before the LLM
    ever sees the tool, authorization at call time, and human approval for L3+.
    """

    L1_READ = "L1"
    L2_LOW = "L2"
    L3_MEDIUM = "L3"
    L4_HIGH = "L4"
    L5_FORBIDDEN = "L5"

    @property
    def level(self) -> int:
        """Numeric level, for comparison against a user's ``max_tool_risk``."""
        return int(self.value[1:])

    @property
    def requires_approval(self) -> bool:
        """Whether invoking this tool must interrupt the graph for a human."""
        return self.level >= RiskClass.L3_MEDIUM.level

    @property
    def requires_dual_approval(self) -> bool:
        """Whether two distinct approvers are required (L4)."""
        return self.level >= RiskClass.L4_HIGH.level

    @property
    def is_exposable(self) -> bool:
        """Whether this tool may appear in a catalogue at all.

        L5 is never exposed to an agent under any circumstance, whatever the
        caller's permissions.
        """
        return self is not RiskClass.L5_FORBIDDEN


class TenantId(StrEnum):
    """Known tenants (ADR-016).

    Declared as an enum in development so that a typo fails fast. Production
    would resolve tenants from the identity provider instead; the type alias
    keeps call sites unchanged.
    """

    MALI = "telecomx-ml"
    SENEGAL = "telecomx-sn"
    SANDBOX = "telecomx-dev"
