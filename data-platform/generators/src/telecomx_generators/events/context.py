"""Shared state of a generation run: the network, the population, the KPI model.

The :class:`KpiModel` is the heart of the coupling between streams. It answers one
question deterministically: *what are the service-quality indicators of this cell at this
instant?* The ``network.metrics`` stream publishes them (with noise); the CDR stream draws
call outcomes from the same drop rate; the data-session stream draws terminations from the
same packet loss; the equipment stream derives link utilisation from the same traffic.
Because every stream reads the same function, a degradation shows up everywhere at once.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from telecom_common.domain.entities import CellRef, EquipmentRef
from telecom_common.domain.enums import Technology
from telecom_common.reference import ReferenceCatalog
from telecomx_generators.events.config import GeneratorConfig
from telecomx_generators.events.population import Population, anchor_site_id
from telecomx_generators.events.rand import stream_rng
from telecomx_generators.events.scenarios import (
    CELL_DEGRADED,
    CELL_POST_FIX,
    CellDegradation,
    FraudBurst,
    GroundTruth,
    Scenario,
)
from telecomx_generators.events.seasonality import load_factor
from telecomx_generators.reference.build import ANCHORS

__all__ = ["CellKpis", "GenContext", "KpiModel"]


@dataclass(frozen=True, slots=True)
class CellKpis:
    """Noise-free service indicators of one cell at one instant."""

    demand: float  # offered load as a share of capacity (can exceed 1)
    served: float  # carried load as a share of capacity (capped below 1)
    congestion: float  # 0 healthy .. 1 saturated
    latency_ms: float
    packet_loss_pct: float
    drop_rate_pct: float
    call_setup_failure_pct: float
    throughput_factor: float
    availability_pct: float
    degradation: float  # scenario weight, 0 when no scenario touches the cell


class KpiModel:
    """Service-quality indicators of every cell, as a pure function of time."""

    def __init__(
        self,
        catalog: ReferenceCatalog,
        tenant_id: str,
        seed: int,
        degradations: tuple[CellDegradation, ...],
    ) -> None:
        self._catalog = catalog
        self._tenant = tenant_id
        self._seed = seed
        self._degradations = {d.cell_id: d for d in degradations}
        self._latency_ref = {t.technology: t.reference_latency_ms for t in catalog.technologies}

    @lru_cache(maxsize=8192)  # noqa: B019 - lives as long as the generator
    def scale(self, cell_id: str) -> float:
        """How loaded a cell is for a given traffic level. Stable per cell.

        Most cells run comfortably; a few are *hot* and saturate at the evening peak, which
        is what a capacity forecast exists to find. The two anchors are set by hand:
        CELL-0421 runs at about 47 % at 18:00 (the PRD example: 48 291 MB on a 100 GB/h
        cell), CELL-0721 would demand about 128 % of its capacity at the peak.
        """
        if cell_id == f"CELL-{ANCHORS.degraded_cell:04d}":
            return 0.29
        if cell_id == f"CELL-{ANCHORS.saturating_cell:04d}":
            return 0.67
        rng = stream_rng(self._seed, "cell-scale", cell_id)
        cell = self._catalog.get("cells", cell_id, self._tenant)
        if cell.technology is Technology.G2:
            return rng.uniform(0.10, 0.20)
        return rng.uniform(0.45, 0.70) if rng.random() < 0.05 else rng.uniform(0.18, 0.34)

    def at(self, cell_id: str, when: datetime) -> CellKpis:
        """Indicators of ``cell_id`` at ``when`` (resolved to the minute)."""
        return self._at(cell_id, int(when.timestamp() // 60))

    @lru_cache(maxsize=262_144)  # noqa: B019
    def _at(self, cell_id: str, minute: int) -> CellKpis:
        when = datetime.fromtimestamp(minute * 60 + 30, tz=UTC)
        cell = self._catalog.get("cells", cell_id, self._tenant)

        demand = self.scale(cell_id) * load_factor("data", when)
        served = min(demand, 0.98)
        congestion = max(0.0, min(1.0, (demand - 0.85) / 0.30))

        base_latency = self._latency_ref[cell.technology] * 0.8
        latency = base_latency * (1 + 0.6 * served**3) + 120 * congestion
        packet_loss = 0.6 + 2.5 * served**2 + 8 * congestion
        drop_rate = 0.5 + 1.4 * served**2 + 6 * congestion
        setup_failure = 0.6 + 1.0 * served**2 + 4 * congestion
        throughput = 1.0
        availability = 100.0

        scenario = self._degradations.get(cell_id)
        degradation = 0.0
        if scenario is not None:
            w_degraded, w_post_fix = scenario.weights(when)
            w_base = 1.0 - w_degraded - w_post_fix
            degradation = w_degraded

            def blend(base: float, key: str) -> float:
                return (
                    w_base * base
                    + w_degraded * CELL_DEGRADED[key]
                    + w_post_fix * CELL_POST_FIX[key]
                )

            latency = blend(latency, "latency_ms")
            packet_loss = blend(packet_loss, "packet_loss_pct")
            drop_rate = blend(drop_rate, "drop_rate_pct")
            setup_failure = blend(setup_failure, "call_setup_failure_pct")
            throughput = blend(1.0, "throughput_factor")
            availability = blend(100.0, "availability_pct")

        return CellKpis(
            demand=demand,
            served=served * (1 - 0.3 * degradation),
            congestion=max(congestion, degradation),
            latency_ms=latency,
            packet_loss_pct=packet_loss,
            drop_rate_pct=drop_rate,
            call_setup_failure_pct=setup_failure,
            throughput_factor=throughput,
            availability_pct=availability,
            degradation=degradation,
        )


class GenContext:
    """Everything the streams share during one run."""

    def __init__(
        self,
        catalog: ReferenceCatalog,
        tenant_id: str,
        config: GeneratorConfig,
        scenarios: tuple[Scenario, ...] = (),
    ) -> None:
        self.catalog = catalog
        self.tenant_id = tenant_id
        self.config = config
        self.scenarios = scenarios
        self.degradations = tuple(s for s in scenarios if isinstance(s, CellDegradation))
        self.frauds = tuple(s for s in scenarios if isinstance(s, FraudBurst))

        self.population = Population(catalog, tenant_id, config.seed, config.population_size)
        self.kpi = KpiModel(catalog, tenant_id, config.seed, self.degradations)
        self.truth = GroundTruth(scenarios=[s.truth() for s in scenarios])
        self._home_index: dict[str, list[int]] | None = None

        self._cells = {c.cell_id: c for c in catalog.cells if c.tenant_id == tenant_id}
        self._sites = {s.site_id: s for s in catalog.sites if s.tenant_id == tenant_id}
        self.cells_by_site: dict[str, list[CellRef]] = {}
        for cell in self._cells.values():
            self.cells_by_site.setdefault(cell.site_id, []).append(cell)

        self.periodic_cells = self._select_periodic_cells()
        periodic_sites = {self._cells[c].site_id for c in self.periodic_cells}
        self._equipment = {e.equipment_id: e for e in catalog.equipment if e.tenant_id == tenant_id}
        self.equipment_by_site: dict[str, list[EquipmentRef]] = {}
        for item in self._equipment.values():
            self.equipment_by_site.setdefault(item.site_id, []).append(item)
        self.periodic_equipment = tuple(
            sorted(e.equipment_id for e in self._equipment.values() if e.site_id in periodic_sites)
        )
        self.region_of_site = {s.site_id: s.region_code for s in self._sites.values()}

        # Precomputed once: the background alarm process runs for every minute and looks
        # back over hundreds of earlier ones, so rebuilding these per call would dominate.
        self.alarm_pool = tuple(a for a in catalog.alarm_types if a.domain != "PLATFORM")
        self.sorted_sites = tuple(sorted(self.region_of_site))

    def _select_periodic_cells(self) -> tuple[str, ...]:
        active = sorted(c.cell_id for c in self._cells.values() if c.is_active)
        count = max(1, round(len(active) * self.config.periodic_fraction))
        chosen = set(stream_rng(self.config.seed, "periodic-cells").sample(active, count))
        # The demonstration scenario must always have its cells, whatever the fraction.
        chosen.update(c.cell_id for c in self.cells_by_site.get(anchor_site_id(), []))
        chosen.add(f"CELL-{ANCHORS.saturating_cell:04d}")
        chosen.update(d.cell_id for d in self.degradations)
        return tuple(sorted(chosen))

    # -- Lookup helpers -------------------------------------------------------

    def cell(self, cell_id: str) -> CellRef:
        return self._cells[cell_id]

    def equipment(self, equipment_id: str) -> EquipmentRef:
        return self._equipment[equipment_id]

    def subscribers_on_cell(self, cell_id: str) -> list[int]:
        """Indices of the subscribers whose home cell is ``cell_id`` (cached)."""
        if self._home_index is None:
            index: dict[str, list[int]] = {}
            for i in range(self.population.size):
                index.setdefault(self.population.subscriber(i).home_cell, []).append(i)
            self._home_index = index
        return self._home_index.get(cell_id, [])

    def region_of_cell(self, cell_id: str) -> str:
        return self.region_of_site[self._cells[cell_id].site_id]

    def site_demand_mbps(self, site_id: str, when: datetime) -> float:
        """Traffic a site's cells carry, in Mbps (1 MB/hour = 8/3600 Mbps)."""
        return sum(
            self.kpi.at(c.cell_id, when).served * c.capacity_mb_per_hour * 8 / 3600
            for c in self.cells_by_site[site_id]
            if c.is_active
        )

    def serving_cell(self, rng: random.Random, home_cell: str, region: str) -> str:
        """Where a subscriber is when an event happens: at home 70 % of the time."""
        if rng.random() < 0.7:
            return home_cell
        return self.population.cell_in_region(region, rng.random())

    def arrival_delay(self, rng: random.Random) -> timedelta:
        """Time between an event happening and its reaching the platform."""
        roll = rng.random()
        cfg = self.config
        if roll < cfg.very_late_fraction:
            return timedelta(
                seconds=rng.uniform(cfg.max_delay_seconds + 1, cfg.very_late_max_seconds)
            )
        if roll < cfg.very_late_fraction + cfg.late_fraction:
            return timedelta(seconds=rng.uniform(5, cfg.max_delay_seconds))
        return timedelta(seconds=min(4.0, rng.lognormvariate(-0.5, 0.6)))
