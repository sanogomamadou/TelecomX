"""Network streams: radio metrics, alarms and equipment telemetry."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from telecom_common.contracts.generated.equipment_telemetry_v1 import EquipmentTelemetry
from telecom_common.contracts.generated.network_alarms_v1 import NetworkAlarm
from telecom_common.contracts.generated.network_metrics_v1 import NetworkMetrics
from telecom_common.domain.entities import EquipmentRef
from telecomx_generators.events.config import PRODUCTION_RATES
from telecomx_generators.events.context import GenContext
from telecomx_generators.events.ids import alarm_id
from telecomx_generators.events.rand import poisson, stream_rng
from telecomx_generators.events.scenarios import CellDegradation
from telecomx_generators.events.seasonality import load_factor
from telecomx_generators.events.streams._common import (
    BUCKET_SECONDS,
    envelope,
    stamps_in_bucket,
)

__all__ = ["alarms", "equipment_telemetry", "network_metrics"]

_ALARM_LOOKBACK_MINUTES = 240


def _noise(rng: Any, sigma: float) -> float:
    return float(rng.lognormvariate(0.0, sigma))


# --- network.metrics ----------------------------------------------------------


def network_metrics(ctx: GenContext, bucket_start: datetime) -> list[NetworkMetrics]:
    """One record per monitored cell, each minute."""
    minute = int(bucket_start.timestamp() // BUCKET_SECONDS)
    rng = stream_rng(ctx.config.seed, "network.metrics", minute)
    technologies = {t.technology: t.downlink_mbps for t in ctx.catalog.technologies}
    events: list[NetworkMetrics] = []

    for cell_id in ctx.periodic_cells:
        cell = ctx.cell(cell_id)
        # Draws happen for every listed cell, in a fixed order, so that skipping an
        # inactive one never shifts the random sequence of the others.
        jitter = rng.uniform(0, 3)
        noise = [_noise(rng, s) for s in (0.04, 0.06, 0.15, 0.15, 0.15, 0.04)]
        signal_noise = rng.gauss(0, 1.0)
        if not cell.is_active:  # pragma: no cover - periodic cells are chosen among active ones
            continue

        when = bucket_start + timedelta(seconds=jitter)
        kpi = ctx.kpi.at(cell_id, when)
        users = min(cell.capacity_users, round(cell.capacity_users * kpi.served**1.1 * noise[0]))
        traffic = cell.capacity_mb_per_hour / 60 * kpi.served * noise[1]
        users_for_share = max(users, 1)
        throughput = min(
            technologies[cell.technology],
            cell.capacity_mb_per_hour
            * 8
            / 3600
            * 0.9
            * kpi.throughput_factor
            * (1 - 0.3 * kpi.served)
            / users_for_share,
        )
        base_signal = -70 - 25 * stream_rng(ctx.config.seed, "rssi", cell_id).random()
        events.append(
            NetworkMetrics(
                **envelope(ctx, rng, when, f"ems-radio-{ctx.region_of_cell(cell_id).lower()}-01"),
                cell_id=cell_id,
                site_id=cell.site_id,
                region=ctx.region_of_cell(cell_id),
                technology=cell.technology,
                traffic_mb=round(traffic, 3),
                active_users=users,
                latency_ms=round(min(5000.0, kpi.latency_ms * noise[2] ** 0.5), 2),
                packet_loss_pct=round(min(100.0, kpi.packet_loss_pct * noise[3]), 3),
                drop_rate_pct=round(min(100.0, kpi.drop_rate_pct * noise[4]), 3),
                call_setup_failure_pct=round(min(100.0, kpi.call_setup_failure_pct * noise[3]), 3),
                throughput_mbps=round(max(0.0, throughput), 3),
                signal_strength_dbm=round(max(-140.0, min(-40.0, base_signal + signal_noise)), 1),
                prb_utilization_pct=round(min(100.0, kpi.served * 100 * noise[5]), 2),
                availability_pct=round(kpi.availability_pct, 2),
            )
        )
    return events


# --- network.alarms ------------------------------------------------------------

_SEVERITY_WEIGHT = {"CRITICAL": 0.5, "MAJOR": 2.0, "MINOR": 3.0, "WARNING": 4.0, "INFO": 1.0}
_EQUIPMENT_FOR_DOMAIN = {
    "TRANSPORT": ("ROUTER", "MICROWAVE_LINK", "FIBER_TERMINAL"),
    "POWER": ("POWER_SUPPLY", "BATTERY", "GENSET"),
    "ENVIRONMENT": ("HVAC",),
}


def _background_opens(ctx: GenContext, minute: int) -> list[dict[str, Any]]:
    """Alarms of the background process that *open* in this minute.

    A pure function of ``(seed, minute)``, so a later bucket can recompute the alarms of
    earlier ones to find which of them clear now, without any stored state.
    """
    when = datetime.fromtimestamp(minute * BUCKET_SECONDS, tz=UTC)
    rng = stream_rng(ctx.config.seed, "alarm.open", minute)
    lam = (
        PRODUCTION_RATES["alarm"]
        * ctx.config.speed_factor
        * BUCKET_SECONDS
        * load_factor("alarm", when)
    )
    count = poisson(rng, lam)
    if count == 0:
        return []  # by far the commonest case: no work at all for this minute
    types = ctx.alarm_pool
    weights = [_SEVERITY_WEIGHT[a.default_severity] for a in types]
    sites = ctx.sorted_sites
    opened: list[dict[str, Any]] = []
    for position, stamp in enumerate(stamps_in_bucket(rng, when, count)):
        kind = rng.choices(types, weights)[0]
        site = rng.choice(sites)
        cell_id = equipment_id = None
        if kind.domain == "RADIO":
            cell_id = rng.choice(ctx.cells_by_site[site]).cell_id
        elif kind.domain in _EQUIPMENT_FOR_DOMAIN:
            wanted = _EQUIPMENT_FOR_DOMAIN[kind.domain]
            candidates = [
                e.equipment_id
                for e in ctx.equipment_by_site.get(site, [])
                if e.equipment_type in wanted
            ]
            equipment_id = rng.choice(candidates) if candidates else None
        opened.append(
            {
                "kind": kind,
                "stamp": stamp,
                "position": position,
                "site": site,
                "cell_id": cell_id,
                "equipment_id": equipment_id,
                "event_envelope": envelope(ctx, rng, stamp, "ems-fault-01"),
                "duration": timedelta(minutes=min(240.0, max(2.0, rng.expovariate(1 / 45)))),
            }
        )
    return opened


def _alarm(ctx: GenContext, spec: dict[str, Any], *, cleared_at: datetime | None) -> NetworkAlarm:
    kind = spec["kind"]
    site = spec["site"]
    fields = dict(spec["event_envelope"])
    if cleared_at is not None:
        rng = stream_rng(ctx.config.seed, "alarm.clear", fields["event_id"])
        fields = envelope(ctx, rng, cleared_at, "ems-fault-01")
    return NetworkAlarm(
        **fields,
        # An incident keeps one alarm id from opening to clearing: the clear event must
        # name the alarm it closes, not a new one.
        alarm_id=alarm_id(spec.get("id_stamp", spec["stamp"]), spec["position"]),
        alarm_code=kind.alarm_code,
        severity=kind.default_severity,
        domain=kind.domain,
        site_id=site,
        cell_id=spec["cell_id"],
        equipment_id=spec["equipment_id"],
        region=ctx.region_of_site[site],
        description=f"{kind.label} sur {site}",
        status="CLEARED" if cleared_at else "ACTIVE",
        cleared_timestamp=cleared_at,
        source="EMS",
        correlation_id=spec.get("correlation_id"),
    )


#: (alarm code, KPI attribute, threshold). Raised when the KPI crosses the threshold
#: upwards, cleared when it comes back below.
_SCENARIO_ALARMS = (
    ("PACKET_LOSS_HIGH", "packet_loss_pct", 5.0),
    ("BACKHAUL_CONGESTION", "latency_ms", 100.0),
    ("HIGH_DROP_RATE", "drop_rate_pct", 5.0),
)


def _scenario_alarms(ctx: GenContext, bucket_start: datetime) -> list[NetworkAlarm]:
    """Alarms a degradation raises when its KPIs cross thresholds, and clears on recovery."""
    events: list[NetworkAlarm] = []
    minute = int(bucket_start.timestamp() // BUCKET_SECONDS)
    previous = bucket_start - timedelta(seconds=BUCKET_SECONDS)
    for scenario in ctx.degradations:
        now, before = (
            ctx.kpi.at(scenario.cell_id, bucket_start),
            ctx.kpi.at(scenario.cell_id, previous),
        )
        for position, (code, attribute, threshold) in enumerate(_SCENARIO_ALARMS):
            value, prior = getattr(now, attribute), getattr(before, attribute)
            opening, closing = prior < threshold <= value, prior >= threshold > value
            if not (opening or closing):
                continue
            kind = next(a for a in ctx.catalog.alarm_types if a.alarm_code == code)
            rng = stream_rng(ctx.config.seed, "alarm.scenario", minute, position)
            stamp = bucket_start + timedelta(seconds=rng.uniform(0, 20))
            spec = {
                "kind": kind,
                "stamp": stamp,
                "id_stamp": scenario.start,
                "position": 90 + position,  # high positions: never collide with background ones
                "site": scenario.site_id,
                "cell_id": scenario.cell_id if kind.domain == "RADIO" else None,
                "equipment_id": scenario.router_id if kind.domain == "TRANSPORT" else None,
                "event_envelope": envelope(ctx, rng, stamp, "ems-fault-01"),
                "correlation_id": f"CORR-{scenario.cell_id}-{scenario.start:%Y%m%d%H%M}",
            }
            events.append(_alarm(ctx, spec, cleared_at=stamp if closing else None))
            ctx.truth.record(
                kind="alarm_open" if opening else "alarm_clear",
                alarm_code=code,
                cell_id=scenario.cell_id,
                at=stamp.isoformat(),
            )
    return events


def alarms(ctx: GenContext, bucket_start: datetime) -> list[NetworkAlarm]:
    """Background alarms (open and clear) plus those raised by active scenarios."""
    minute = int(bucket_start.timestamp() // BUCKET_SECONDS)
    events = [_alarm(ctx, spec, cleared_at=None) for spec in _background_opens(ctx, minute)]
    # Clears: recompute the openings of earlier minutes and keep those ending in this one.
    for earlier in range(minute - _ALARM_LOOKBACK_MINUTES, minute):
        for spec in _background_opens(ctx, earlier):
            end = spec["stamp"] + spec["duration"]
            if bucket_start <= end < bucket_start + timedelta(seconds=BUCKET_SECONDS):
                events.append(_alarm(ctx, spec, cleared_at=end))
    events.extend(_scenario_alarms(ctx, bucket_start))
    return events


# --- device.events -------------------------------------------------------------

_LIFETIME_SECONDS = 86_400
_REPORT_EVERY_MINUTES = 4

_AMBIENT = {
    "BKO": 29.0,
    "SKS": 27.0,
    "KLK": 28.0,
    "SGU": 29.0,
    "KYS": 30.0,
    "MPT": 30.0,
    "TBT": 33.0,
    "GAO": 34.0,
    "KDL": 35.0,
}


def _crc_extra(scenario: CellDegradation, minute: int) -> int:
    """Cumulative CRC errors a congested link accumulated since the scenario began."""
    total = 0.0
    start = int(scenario.start.timestamp() // 60)
    for m in range(start, minute + 1):
        weight = scenario.intensity(datetime.fromtimestamp(m * 60, tz=scenario.start.tzinfo))
        total += 900 * weight
    return int(total)


def _equipment_fields(
    ctx: GenContext, item: EquipmentRef, when: datetime, rng: Any
) -> dict[str, Any]:
    site = ctx.catalog.get("sites", item.site_id, ctx.tenant_id)
    region = ctx.region_of_site[item.site_id]
    demand = ctx.site_demand_mbps(item.site_id, when)
    util = min(1.0, demand / site.backhaul_capacity_mbps)
    weight = max(
        (s.intensity(when) for s in ctx.degradations if s.site_id == item.site_id), default=0.0
    )
    day_phase = 0.5 + 0.5 * math.sin((when.hour - 9) / 24 * 2 * math.pi)
    ambient = _AMBIENT.get(region, 29.0) + 5 * day_phase

    boot_period = stream_rng(ctx.config.seed, "boot", item.equipment_id)
    period = boot_period.randint(60, 200) * _LIFETIME_SECONDS
    offset = boot_period.randrange(period)
    uptime = int((when.timestamp() + offset) % period)

    fields: dict[str, Any] = {
        "uptime_seconds": uptime,
        "days_since_maintenance": max(0, (when.date() - item.last_maintenance_date).days),
        "operational_status": "DEGRADED"
        if weight > 0.5 and item.equipment_type == "ROUTER"
        else "NORMAL",
    }
    kind = item.equipment_type
    link_util = (util * (1 - weight) + 0.97 * weight) * 100
    crc_base = int(uptime / 60 * 0.4)

    if kind in {"BBU", "ROUTER", "SWITCH"}:
        cpu = (28 + 30 * util) * (1 - weight) + 94 * weight
        fields["cpu_utilization_pct"] = round(min(100.0, cpu * _noise(rng, 0.03)), 1)
        fields["memory_utilization_pct"] = round(
            min(100.0, (48 + 8 * util) * (1 - weight) + 78 * weight), 1
        )
        fields["temperature_celsius"] = round(ambient + 0.15 * cpu + rng.gauss(0, 0.5), 1)
        fields["power_consumption_watts"] = round(300 + 150 * util + 40 * weight, 1)
        fields["fan_status"] = "OK"
    if kind in {"ROUTER", "SWITCH", "MICROWAVE_LINK", "FIBER_TERMINAL"}:
        fields["link_utilization_pct"] = round(min(100.0, link_util * _noise(rng, 0.02)), 1)
        fields["packet_error_rate"] = round(0.02 + 0.08 * (util > 0.8) + 5 * weight, 3)
        extra = max(
            (
                _crc_extra(s, int(when.timestamp() // 60))
                for s in ctx.degradations
                if s.site_id == item.site_id
            ),
            default=0,
        )
        fields["crc_error_count"] = crc_base + extra
    if kind == "POWER_SUPPLY":
        fields["input_voltage"] = round(48 + rng.gauss(0, 0.3), 2)
        fields["power_consumption_watts"] = round(900 + 250 * util, 1)
        fields["temperature_celsius"] = round(ambient + 6, 1)
    if kind == "BATTERY":
        fields["battery_level_pct"] = round(min(100.0, 99.5 + rng.gauss(0, 0.2)), 1)
        fields["battery_cycles"] = int(uptime / _LIFETIME_SECONDS * 0.3)
        fields["temperature_celsius"] = round(ambient + 2, 1)
    if kind == "GENSET":
        fields["temperature_celsius"] = round(ambient, 1)
    if kind == "HVAC":
        fields["temperature_celsius"] = round(26 + rng.gauss(0, 0.7), 1)
        fields["fan_status"] = "OK"
        fields["power_consumption_watts"] = round(800 + rng.gauss(0, 30), 1)
    return fields


def equipment_telemetry(ctx: GenContext, bucket_start: datetime) -> list[EquipmentTelemetry]:
    """Each monitored piece of equipment reports every few minutes, at a fixed phase."""
    minute = int(bucket_start.timestamp() // BUCKET_SECONDS)
    rng = stream_rng(ctx.config.seed, "device.events", minute)
    events: list[EquipmentTelemetry] = []
    for equipment_id in ctx.periodic_equipment:
        item = ctx.equipment(equipment_id)
        phase = stream_rng(ctx.config.seed, "eq-phase", equipment_id).randrange(
            _REPORT_EVERY_MINUTES
        )
        jitter = rng.uniform(0, 20)
        if minute % _REPORT_EVERY_MINUTES != phase:
            continue
        when = bucket_start + timedelta(seconds=jitter)
        events.append(
            EquipmentTelemetry(
                **envelope(ctx, rng, when, "ems-infra-01"),
                equipment_id=equipment_id,
                site_id=item.site_id,
                equipment_type=item.equipment_type,
                vendor=item.vendor,
                model=item.model,
                **_equipment_fields(ctx, item, when, rng),
            )
        )
    return events
