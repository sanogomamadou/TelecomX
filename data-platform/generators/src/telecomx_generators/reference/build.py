"""Build the synthetic reference data: regions, sites, cells, equipment and catalogues.

Determinism
-----------
The same seed yields byte-identical output, which is what lets CI regenerate the data
and compare it with what is committed. Each part of the network draws from its own
random stream (``Random("<seed>:<stream>")``), so changing how cells are assigned does
not reshuffle the sites. Determinism holds for a given Python minor version: the
standard library does not promise identical streams across minor versions, which is
why the repository pins Python 3.11.

Scenario anchors
----------------
The demonstration scenario of the PRD names specific objects: ``CELL-0421`` on
``SITE-102`` degrading because of backhaul congestion, ``ROUTER-082`` with a high CPU,
and ``CELL-0721`` about to saturate. A random network would not contain them, so they
are *constructed* (see :class:`ScenarioAnchors`), with the properties that make the
story plausible. For instance the backhaul of ``SITE-102`` is smaller than the traffic
its 4G cells can demand at full load, so it can genuinely congest. The construction is
explicit and tested, rather than hoped for.

Everything produced is fictitious. See :mod:`telecomx_generators.reference.profiles`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from telecom_common.domain.entities import (
    AlarmTypeRef,
    BundleRef,
    CellRef,
    EquipmentRef,
    RegionRef,
    SiteRef,
    TechnologyRef,
    TenantRef,
)
from telecom_common.domain.enums import BackhaulType, SiteType, Technology
from telecom_common.domain.identifiers import format_cell_id, format_site_id
from telecom_common.reference import CatalogError, ReferenceCatalog
from telecomx_generators.reference.profiles import (
    ALARM_TYPES,
    BUNDLES,
    REFERENCE_DATE,
    REGION_PROFILES,
    TECHNOLOGIES,
    TENANTS,
    RegionProfile,
)

__all__ = [
    "ANCHORS",
    "CELLS_BY_TECHNOLOGY",
    "DEFAULT_SEED",
    "SITE_COUNT",
    "TENANT_ID",
    "TOTAL_SUBSCRIBERS",
    "ScenarioAnchors",
    "build_catalog",
]

DEFAULT_SEED: Final = 42
TENANT_ID: Final = "telecomx-ml"

#: Figures of the PRD (section 2.1). Cells by technology sum to 4 821.
SITE_COUNT: Final = 1610
CELLS_BY_TECHNOLOGY: Final = {
    Technology.G2: 1205,
    Technology.G3: 1412,
    Technology.G4: 1908,
    Technology.G5: 296,
}
TOTAL_SUBSCRIBERS: Final = 12_400_000
TOTAL_POPULATION: Final = 22_000_000

MAX_SECTORS_PER_TECHNOLOGY: Final = 3
_LAT_RANGE: Final = (10.1, 25.0)
_LON_RANGE: Final = (-12.3, 4.3)


@dataclass(frozen=True, slots=True)
class ScenarioAnchors:
    """Objects the demonstration scenario refers to by name (PRD section 8)."""

    degraded_site: int = 102  # SITE-102: where the backhaul congestion unfolds
    degraded_cell: int = 421  # CELL-0421: the cell whose KPIs degrade
    saturating_cell: int = 721  # CELL-0721: forecast to saturate tomorrow evening
    saturating_site_hint: int = 205  # a Bamako site to host it (first with 4G from here)
    router: int = 82  # ROUTER-082: the router of the degraded site
    cell_capacity_mb_per_hour: int = 102_400  # 100 GB/h, the figure of the PRD examples
    cell_capacity_users: int = 1_200  # leaves room for the 842 active users of the example
    # Below what the cells can demand at full load (~606 Mbps for the three 4G cells of
    # SITE-102), so the link can genuinely congest. A tier higher (622) would not: the
    # cells could never saturate it. A test pins this relation.
    backhaul_capacity_mbps: float = 311.0
    min_4g_cells_on_degraded_site: int = 3


ANCHORS: Final = ScenarioAnchors()


# --- Helpers ------------------------------------------------------------------


def _rng(seed: int, stream: str) -> random.Random:
    """An independent, reproducible random stream for one part of the build."""
    return random.Random(f"{seed}:{stream}")  # noqa: S311 - synthetic data, not security


def _apportion(total: int, weights: list[float]) -> list[int]:
    """Split ``total`` into integers proportional to ``weights``, summing exactly.

    Largest-remainder method: round down, then hand the leftover units to the
    largest fractional parts. Plain rounding would drift from the target total.
    """
    scale = sum(weights)
    quotas = [total * w / scale for w in weights]
    shares = [int(q) for q in quotas]
    order = sorted(range(len(weights)), key=lambda i: quotas[i] - shares[i], reverse=True)
    for index in order[: total - sum(shares)]:
        shares[index] += 1
    return shares


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def _round_to(value: float, step: int) -> int:
    return int(round(value / step) * step)


# --- Sites --------------------------------------------------------------------


@dataclass
class _Site:
    """A site under construction. Mutable only so that anchors can be applied."""

    number: int
    profile: RegionProfile
    is_urban: bool
    site_type: SiteType
    latitude: float
    longitude: float
    commissioned: date
    backup_power: bool
    backhaul: BackhaulType
    backhaul_mbps: float

    @property
    def site_id(self) -> str:
        return format_site_id(self.number)


_URBAN_TYPES: Final = (SiteType.ROOFTOP, SiteType.MICRO, SiteType.INDOOR, SiteType.MACRO)
_URBAN_TYPE_WEIGHTS: Final = (0.40, 0.25, 0.10, 0.25)


def _build_sites(seed: int) -> list[_Site]:
    rng = _rng(seed, "sites")
    counts = _apportion(
        SITE_COUNT, [0.7 * p.subscriber_share + 0.3 * p.area_share for p in REGION_PROFILES]
    )
    earliest = date(2007, 1, 1)
    span_days = (REFERENCE_DATE - timedelta(days=90) - earliest).days

    sites: list[_Site] = []
    for profile, count in zip(REGION_PROFILES, counts, strict=True):
        for _ in range(count):
            is_urban = rng.random() < profile.urbanization
            if is_urban:
                site_type = rng.choices(_URBAN_TYPES, _URBAN_TYPE_WEIGHTS)[0]
            else:
                site_type = rng.choices((SiteType.MACRO, SiteType.MICRO), (0.9, 0.1))[0]

            p_fiber = min(1.0, profile.fiber_share * (1.4 if is_urban else 0.6))
            roll = rng.random()
            if roll < p_fiber:
                backhaul = BackhaulType.FIBER
            elif not is_urban and roll < p_fiber + profile.satellite_share:
                backhaul = BackhaulType.SATELLITE
            else:
                backhaul = BackhaulType.MICROWAVE

            if backhaul is BackhaulType.FIBER:
                large = site_type in {SiteType.MACRO, SiteType.ROOFTOP}
                mbps = rng.choices((1000.0, 10000.0), (0.6, 0.4) if large else (1.0, 0.0))[0]
            elif backhaul is BackhaulType.MICROWAVE:
                mbps = rng.choices((155.0, 311.0, 622.0, 1000.0), (0.2, 0.3, 0.35, 0.15))[0]
            else:
                mbps = rng.choices((10.0, 20.0, 50.0), (0.4, 0.4, 0.2))[0]

            sites.append(
                _Site(
                    number=len(sites) + 1,
                    profile=profile,
                    is_urban=is_urban,
                    site_type=site_type,
                    latitude=round(
                        _clamp(rng.gauss(profile.centroid[0], profile.spread_deg), _LAT_RANGE), 5
                    ),
                    longitude=round(
                        _clamp(rng.gauss(profile.centroid[1], profile.spread_deg), _LON_RANGE), 5
                    ),
                    commissioned=earliest + timedelta(days=rng.randint(0, span_days)),
                    backup_power=rng.random() < profile.backup_power_share,
                    backhaul=backhaul,
                    backhaul_mbps=mbps,
                )
            )

    anchor = sites[ANCHORS.degraded_site - 1]
    anchor.is_urban = True
    anchor.site_type = SiteType.MACRO
    anchor.backup_power = True
    anchor.backhaul = BackhaulType.MICROWAVE
    anchor.backhaul_mbps = ANCHORS.backhaul_capacity_mbps
    return sites


# --- Cells --------------------------------------------------------------------

_TYPE_WEIGHT: Final[dict[SiteType, dict[Technology, float]]] = {
    SiteType.MACRO: {
        Technology.G2: 1.0,
        Technology.G3: 1.0,
        Technology.G4: 1.2,
        Technology.G5: 0.8,
    },
    SiteType.MICRO: {
        Technology.G2: 0.4,
        Technology.G3: 0.6,
        Technology.G4: 1.0,
        Technology.G5: 0.8,
    },
    SiteType.INDOOR: {
        Technology.G2: 0.0,
        Technology.G3: 0.3,
        Technology.G4: 0.8,
        Technology.G5: 0.6,
    },
    SiteType.ROOFTOP: {
        Technology.G2: 0.6,
        Technology.G3: 1.0,
        Technology.G4: 1.2,
        Technology.G5: 1.0,
    },
}


def _technology_weight(site: _Site, technology: Technology) -> float:
    """How likely ``site`` is to host a cell of ``technology``."""
    weight = _TYPE_WEIGHT[site.site_type][technology]
    if technology is Technology.G5:
        return weight if site.is_urban else 0.0  # 5G is an urban-only layer
    if technology is Technology.G2 and not site.is_urban:
        return weight * 1.5  # legacy coverage layer matters more in rural areas
    return weight


def _assign_cells(sites: list[_Site], seed: int) -> list[dict[Technology, int]]:
    """Decide how many cells of each technology every site hosts.

    Totals per technology match the PRD exactly, no site hosts more than
    :data:`MAX_SECTORS_PER_TECHNOLOGY` cells of one technology, no site is left
    empty, and the degraded anchor site hosts enough 4G cells to be congestible.
    """
    rng = _rng(seed, "cells-assign")
    counts: list[dict[Technology, int]] = [dict.fromkeys(Technology, 0) for _ in sites]

    for technology, total in CELLS_BY_TECHNOLOGY.items():
        weights = [_technology_weight(s, technology) for s in sites]
        if sum(1 for w in weights if w > 0) * MAX_SECTORS_PER_TECHNOLOGY < total:
            msg = f"not enough eligible sites to host {total} {technology.label} cells"
            raise CatalogError(msg)
        for _ in range(total):
            index = rng.choices(range(len(sites)), weights)[0]
            counts[index][technology] += 1
            if counts[index][technology] >= MAX_SECTORS_PER_TECHNOLOGY:
                weights[index] = 0.0

    repair = _rng(seed, "cells-repair")

    def donor(technology: Technology, exclude: int) -> int:
        """The busiest site that can spare a cell of ``technology`` and still keep one."""
        candidates = [i for i, c in enumerate(counts) if i != exclude and c[technology] >= 2]
        return max(candidates, key=lambda i: (sum(counts[i].values()), -i))

    def move(technology: Technology, source: int, target: int) -> None:
        counts[source][technology] -= 1
        counts[target][technology] += 1

    for index, site in enumerate(sites):
        if sum(counts[index].values()) > 0:
            continue
        if site.is_urban:
            technology = Technology.G4
        else:
            technology = repair.choice((Technology.G2, Technology.G3, Technology.G4))
        move(technology, donor(technology, exclude=index), index)

    anchor_index = ANCHORS.degraded_site - 1
    while counts[anchor_index][Technology.G4] < ANCHORS.min_4g_cells_on_degraded_site:
        move(Technology.G4, donor(Technology.G4, exclude=anchor_index), anchor_index)

    return counts


@dataclass
class _Cell:
    number: int
    site: _Site
    technology: Technology
    sector: int
    sectors: int


_BANDS: Final = {
    Technology.G2: ("900", "1800"),
    Technology.G3: ("2100", "900"),
    Technology.G4: ("B20-800", "B3-1800", "B7-2600"),
    Technology.G5: ("n78-3500",),
}
_AZIMUTHS: Final = {1: (0,), 2: (0, 180), 3: (0, 120, 240)}
_USERS_RANGE: Final = {
    Technology.G2: (150, 300),
    Technology.G3: (250, 500),
    Technology.G4: (600, 1500),
    Technology.G5: (800, 2000),
}


def _capacity_range(technology: Technology, site_type: SiteType) -> tuple[float, float]:
    """Traffic range (MB/hour) a cell of this technology on this kind of site can carry."""
    large = site_type is SiteType.MACRO
    rooftop = site_type is SiteType.ROOFTOP
    ranges = {
        Technology.G2: (1500.0, 3000.0) if large else (800.0, 1800.0),
        Technology.G3: (12000.0, 25000.0)
        if large
        else (10000.0, 20000.0)
        if rooftop
        else (8000.0, 15000.0),
        Technology.G4: (60000.0, 120000.0)
        if large
        else (50000.0, 90000.0)
        if rooftop
        else (25000.0, 50000.0),
        Technology.G5: (200000.0, 400000.0)
        if large
        else (150000.0, 300000.0)
        if rooftop
        else (80000.0, 160000.0),
    }
    return ranges[technology]


def _swap_numbers(cells: list[_Cell], first: _Cell, number: int) -> None:
    """Give ``first`` the identifier ``number``, handing its own to the previous holder."""
    holder = next(c for c in cells if c.number == number)
    holder.number, first.number = first.number, holder.number


def _build_cells(sites: list[_Site], seed: int) -> list[CellRef]:
    counts = _assign_cells(sites, seed)

    drafts: list[_Cell] = []
    for site, per_technology in zip(sites, counts, strict=True):
        for technology in Technology:
            sectors = per_technology[technology]
            # Fixed before extending: the generator below is consumed lazily, so reading
            # len(drafts) inside it would grow with every element and skip numbers.
            first_number = len(drafts) + 1
            drafts.extend(
                _Cell(first_number + k, site, technology, k, sectors) for k in range(sectors)
            )

    degraded = next(
        c
        for c in drafts
        if c.site.number == ANCHORS.degraded_site and c.technology is Technology.G4
    )
    _swap_numbers(drafts, degraded, ANCHORS.degraded_cell)

    saturating = next(
        c
        for c in drafts
        if c.site.profile.code == "BKO"
        and c.site.number >= ANCHORS.saturating_site_hint
        and c.technology is Technology.G4
    )
    _swap_numbers(drafts, saturating, ANCHORS.saturating_cell)

    rng = _rng(seed, "cells-attributes")
    bands: dict[tuple[int, Technology], str] = {}
    rows: list[CellRef] = []
    for cell in sorted(drafts, key=lambda c: c.number):
        site, technology = cell.site, cell.technology
        band = bands.setdefault((site.number, technology), rng.choice(_BANDS[technology]))
        low, high = _capacity_range(technology, site.site_type)
        users_low, users_high = _USERS_RANGE[technology]
        capacity = _round_to(rng.uniform(low, high), 100)
        users = _round_to(rng.uniform(users_low, users_high), 10)
        active = rng.random() < 0.985

        if cell.number in {ANCHORS.degraded_cell, ANCHORS.saturating_cell}:
            capacity = ANCHORS.cell_capacity_mb_per_hour
            users = ANCHORS.cell_capacity_users
            active = True

        rows.append(
            CellRef(
                tenant_id=TENANT_ID,
                cell_id=format_cell_id(cell.number),
                site_id=site.site_id,
                technology=technology,
                frequency_band=band,
                azimuth_degrees=_AZIMUTHS[cell.sectors][cell.sector],
                capacity_mb_per_hour=capacity,
                capacity_users=users,
                is_active=active,
            )
        )
    return rows


# --- Equipment ----------------------------------------------------------------

#: type -> (expected lifetime in years, model prefix, model series)
_EQUIPMENT: Final[dict[str, tuple[int, str, tuple[str, ...]]]] = {
    "BBU": (10, "BB", ("5200", "6300", "7100")),
    "ROUTER": (8, "RT", ("A200", "A400", "X900")),
    "SWITCH": (8, "SW", ("L2-24", "L3-48")),
    "POWER_SUPPLY": (12, "PS", ("48-2K", "48-4K")),
    "BATTERY": (5, "BT", ("LI-100", "PB-200")),
    "GENSET": (15, "GS", ("15KVA", "30KVA")),
    "HVAC": (10, "AC", ("S12", "S18")),
    "MICROWAVE_LINK": (12, "MW", ("L6", "L11", "E80")),
    "FIBER_TERMINAL": (15, "FT", ("GP-1", "GP-2")),
}
#: Invented vendors. No relation to any real manufacturer.
_VENDORS: Final = ("Nordwave", "Helix Telecom", "Aurora Networks")


def _equipment_types_for(site: _Site, rng: random.Random) -> list[str]:
    types = ["BBU", "ROUTER", "POWER_SUPPLY"]
    if (site.site_type is SiteType.MACRO and rng.random() < 0.6) or (
        site.site_type is SiteType.ROOFTOP and rng.random() < 0.3
    ):
        types.append("HVAC")
    if site.backup_power:
        types.append("BATTERY")
        if not site.is_urban and rng.random() < 0.6:
            types.append("GENSET")
    if site.backhaul is BackhaulType.MICROWAVE:
        types.append("MICROWAVE_LINK")
    elif site.backhaul is BackhaulType.FIBER:
        types.append("FIBER_TERMINAL")
        if site.backhaul_mbps >= 10000.0:
            types.append("SWITCH")
    return types


def _build_equipment(sites: list[_Site], seed: int) -> list[EquipmentRef]:
    rng = _rng(seed, "equipment")
    sequence: dict[str, int] = dict.fromkeys(_EQUIPMENT, 0)
    drafts: list[tuple[str, int, _Site]] = []  # (type, number, site)
    for site in sites:
        for equipment_type in _equipment_types_for(site, rng):
            sequence[equipment_type] += 1
            drafts.append((equipment_type, sequence[equipment_type], site))

    # Anchor: ROUTER-082 is the router of the degraded site.
    anchor_index = next(
        i
        for i, (t, _, s) in enumerate(drafts)
        if t == "ROUTER" and s.number == ANCHORS.degraded_site
    )
    other_index = next(
        i for i, (t, n, _) in enumerate(drafts) if t == "ROUTER" and n == ANCHORS.router
    )
    a_type, a_number, a_site = drafts[anchor_index]
    o_type, o_number, o_site = drafts[other_index]
    drafts[anchor_index] = (a_type, o_number, a_site)
    drafts[other_index] = (o_type, a_number, o_site)

    rows: list[EquipmentRef] = []
    for equipment_type, number, site in sorted(drafts, key=lambda d: (d[0], d[1])):
        lifetime, prefix, series = _EQUIPMENT[equipment_type]
        room = max(0, min(2000, (REFERENCE_DATE - site.commissioned).days - 30))
        installed = site.commissioned + timedelta(days=rng.randint(0, room))
        maintained = installed + timedelta(days=rng.randint(0, (REFERENCE_DATE - installed).days))
        rows.append(
            EquipmentRef(
                tenant_id=TENANT_ID,
                equipment_id=f"{equipment_type}-{number:03d}",
                site_id=site.site_id,
                equipment_type=equipment_type,
                vendor=rng.choice(_VENDORS),
                model=f"{prefix}-{rng.choice(series)}",
                installed_date=installed,
                expected_lifetime_years=lifetime,
                last_maintenance_date=maintained,
            )
        )
    return rows


# --- Assembly -----------------------------------------------------------------


def build_catalog(seed: int = DEFAULT_SEED) -> ReferenceCatalog:
    """Build the complete, validated reference catalogue.

    Only the ``telecomx-ml`` tenant receives a network. The two other tenants exist in
    the ``tenants`` table so that tenant-scoped lookups and isolation tests have
    something to refuse; giving them networks is deferred until those tests exist.

    Args:
        seed: random seed. The committed seeds use :data:`DEFAULT_SEED`.

    Raises:
        CatalogError: if the generated catalogue is incoherent. That would be a bug
            in the generator, and failing here keeps it from ever reaching a CSV.
    """
    sites = _build_sites(seed)
    cells = _build_cells(sites, seed)
    equipment = _build_equipment(sites, seed)

    subscribers = _apportion(TOTAL_SUBSCRIBERS, [p.subscriber_share for p in REGION_PROFILES])
    population = _apportion(TOTAL_POPULATION, [p.population_share for p in REGION_PROFILES])

    catalog = ReferenceCatalog(
        tenants=tuple(
            TenantRef(tenant_id=t, name=n, country_code=c, currency=cur) for t, n, c, cur in TENANTS
        ),
        technologies=tuple(
            TechnologyRef(technology=t, label=label, downlink_mbps=mbps, reference_latency_ms=ms)
            for t, label, mbps, ms in TECHNOLOGIES
        ),
        regions=tuple(
            RegionRef(
                tenant_id=TENANT_ID,
                region_code=p.code,
                region_name=p.name,
                population=pop,
                area_type=p.area_type,
                subscriber_count=subs,
            )
            for p, pop, subs in zip(REGION_PROFILES, population, subscribers, strict=True)
        ),
        sites=tuple(
            SiteRef(
                tenant_id=TENANT_ID,
                site_id=s.site_id,
                region_code=s.profile.code,
                latitude=s.latitude,
                longitude=s.longitude,
                site_type=s.site_type,
                commissioned_date=s.commissioned,
                has_backup_power=s.backup_power,
                backhaul_type=s.backhaul,
                backhaul_capacity_mbps=s.backhaul_mbps,
            )
            for s in sites
        ),
        cells=tuple(cells),
        equipment=tuple(equipment),
        bundles=tuple(
            BundleRef(
                bundle_code=code,
                label=label,
                bundle_type=kind,
                allowance_mb=mb,
                allowance_minutes=minutes,
                price_xof=Decimal(price),
                validity_days=days,
            )
            for code, label, kind, mb, minutes, price, days in BUNDLES
        ),
        alarm_types=tuple(
            AlarmTypeRef(
                alarm_code=code, label=label, domain=domain, default_severity=sev, sop_ref=sop
            )
            for code, label, domain, sev, sop in ALARM_TYPES
        ),
    )

    problems = catalog.validate()
    if problems:
        msg = f"the generator produced an incoherent catalogue: {problems[:5]}"
        raise CatalogError(msg)
    return catalog
