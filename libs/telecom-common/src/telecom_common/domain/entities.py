"""Reference-data entities: the static description of the operator's network.

These are the rows of the ``ref.*`` tables (regions, sites, cells, equipment...), as
typed, validated, immutable objects. They are *not* events: an event says something
happened; a reference row says what exists.

Why they live in the shared library: the generators build them, the Spark job joins
against them, the quality rules (``in_ref``, ``lte_ref``) check events against them,
and the agent tools will read cell capacity from them. One definition, imported by all.

Fields owned by an event contract (``equipment_type``, ``bundle_type``, alarm domain
and severity) are plain strings here, not enums. Their legal values are defined by
the generated contract enums, and re-declaring them would recreate the divergence
ADR-015 exists to prevent. The catalogue checks them against the generated enums
(see :mod:`telecom_common.reference.catalog`).

Tenancy (ADR-016): rows that describe one operator's network carry ``tenant_id``;
rows that are shared across tenants (technologies, alarm catalogue, bundles) do not.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from telecom_common.domain.enums import AreaType, BackhaulType, SiteType, Technology
from telecom_common.domain.identifiers import CellId, EquipmentId, SiteId

__all__ = [
    "AlarmTypeRef",
    "BundleRef",
    "CellRef",
    "EquipmentRef",
    "RegionRef",
    "SiteRef",
    "TechnologyRef",
    "TenantRef",
]

_REGION_CODE = r"^[A-Z]{3}$"
_TENANT_ID = r"^[a-z][a-z0-9-]{2,30}$"


class _Ref(BaseModel):
    """Common configuration: immutable, and strict about unknown columns."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TenantRef(_Ref):
    """An operator (or environment) served by the platform."""

    tenant_id: str = Field(pattern=_TENANT_ID)
    name: str = Field(min_length=1)
    country_code: str = Field(pattern=r"^[A-Z]{2}$", description="ISO 3166-1 alpha-2.")
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217.")


class TechnologyRef(_Ref):
    """Radio generation, with the reference figures a KPI is compared against."""

    technology: Technology
    label: str
    downlink_mbps: float = Field(gt=0, description="Theoretical peak downlink.")
    reference_latency_ms: float = Field(gt=0, description="Typical round-trip latency.")


class RegionRef(_Ref):
    """An administrative region of one tenant."""

    tenant_id: str = Field(pattern=_TENANT_ID)
    region_code: str = Field(pattern=_REGION_CODE)
    region_name: str = Field(min_length=1)
    population: int = Field(gt=0)
    area_type: AreaType
    subscriber_count: int = Field(gt=0)


class SiteRef(_Ref):
    """A physical site hosting one or more cells."""

    tenant_id: str = Field(pattern=_TENANT_ID)
    site_id: SiteId
    region_code: str = Field(pattern=_REGION_CODE)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    site_type: SiteType
    commissioned_date: date
    has_backup_power: bool
    backhaul_type: BackhaulType
    backhaul_capacity_mbps: float = Field(
        gt=0, description="Capacity of the transport link. Key to backhaul congestion diagnosis."
    )


class CellRef(_Ref):
    """One radio cell (a sector of one technology on one site)."""

    tenant_id: str = Field(pattern=_TENANT_ID)
    cell_id: CellId
    site_id: SiteId
    technology: Technology
    frequency_band: str = Field(min_length=1)
    azimuth_degrees: int = Field(ge=0, lt=360)
    capacity_mb_per_hour: int = Field(
        gt=0, description="Traffic the cell can carry. Turns a forecast into a saturation risk."
    )
    capacity_users: int = Field(
        gt=0, description="Simultaneous users the cell can serve. Bound of active_users."
    )
    is_active: bool


class EquipmentRef(_Ref):
    """A piece of site infrastructure (BBU, router, battery, genset...)."""

    tenant_id: str = Field(pattern=_TENANT_ID)
    equipment_id: EquipmentId
    site_id: SiteId
    equipment_type: str = Field(description="One of the EquipmentType symbols of the contract.")
    vendor: str
    model: str
    installed_date: date
    expected_lifetime_years: int = Field(gt=0)
    last_maintenance_date: date


class BundleRef(_Ref):
    """A commercial offer (shared across tenants)."""

    bundle_code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]{1,30}$")
    label: str
    bundle_type: str = Field(description="One of the BundleType symbols of the contract.")
    allowance_mb: float | None = Field(default=None, ge=0)
    allowance_minutes: int | None = Field(default=None, ge=0)
    price_xof: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    validity_days: int = Field(gt=0)


class AlarmTypeRef(_Ref):
    """A kind of alarm, with the procedure that handles it (shared across tenants)."""

    alarm_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,40}$")
    label: str
    domain: str = Field(description="One of the AlarmDomain symbols of the contract.")
    default_severity: str = Field(description="One of the AlarmSeverity symbols of the contract.")
    sop_ref: str | None = Field(
        default=None,
        pattern=r"^SOP-[A-Z]{3}-\d{3}$",
        description=(
            "Procedure to follow. A forward reference: the documents are written for the "
            "RAG corpus in Phase 4, and a test will then check every reference resolves."
        ),
    )
