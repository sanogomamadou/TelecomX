"""Command line: generate synthetic events into JSON Lines files.

Usage:
    python -m telecomx_generators.events --duration-minutes 60 --out-dir build/events
    python -m telecomx_generators.events --start 2026-09-01T17:00:00Z --duration-minutes 180 --scenario all

The default start is the fixed reference date, not the wall clock, so a run is
reproducible. Exit codes: 0 success, 2 invalid arguments or generation failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telecom_common.reference import CatalogError
from telecom_common.timeutils import parse_utc
from telecomx_generators.events.config import GeneratorConfig
from telecomx_generators.events.context import GenContext
from telecomx_generators.events.engine import generate
from telecomx_generators.events.scenarios import CellDegradation, FraudBurst, Scenario
from telecomx_generators.events.sinks import JsonlSink
from telecomx_generators.events.streams._common import BUCKET_SECONDS
from telecomx_generators.reference.build import ANCHORS, DEFAULT_SEED, build_catalog

__all__ = ["main"]

DEFAULT_START = datetime(2026, 9, 1, 17, 0, tzinfo=UTC)
CHUNK = timedelta(minutes=15)


def _scenarios(name: str, start: datetime, duration: timedelta) -> tuple[Scenario, ...]:
    chosen: list[Scenario] = []
    if name in {"degradation", "all"}:
        chosen.append(
            CellDegradation(
                cell_id=f"CELL-{ANCHORS.degraded_cell:04d}",
                site_id=f"SITE-{ANCHORS.degraded_site:03d}",
                start=start + duration * 0.25,
                router_id=f"ROUTER-{ANCHORS.router:03d}",
            )
        )
    if name in {"fraud", "all"}:
        chosen.append(FraudBurst(subscriber_index=5, start=start + duration * 0.5))
    return tuple(chosen)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic TelecomX events.")
    parser.add_argument("--out-dir", type=Path, default=Path("build/events"))
    parser.add_argument("--start", default=DEFAULT_START.isoformat(), help="UTC start (ISO 8601)")
    parser.add_argument("--duration-minutes", type=int, default=60)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--speed-factor", type=float, default=GeneratorConfig().speed_factor)
    parser.add_argument(
        "--scenario", choices=["none", "degradation", "fraud", "all"], default="all"
    )
    args = parser.parse_args(argv)

    try:
        start = parse_utc(args.start)
        duration = timedelta(minutes=args.duration_minutes)
        if duration <= timedelta(0):
            raise ValueError("--duration-minutes must be positive")
        config = GeneratorConfig(seed=args.seed, speed_factor=args.speed_factor)
        catalog = build_catalog(args.seed)
        tenant_id = next(t.tenant_id for t in catalog.tenants)
        ctx = GenContext(catalog, tenant_id, config, _scenarios(args.scenario, start, duration))

        sink = JsonlSink(args.out_dir)
        for path in args.out_dir.glob("*.jsonl"):
            path.unlink()  # a run replaces the previous one, it never appends to it
        cursor, end = start, start + duration
        while cursor < end:
            window_end = min(cursor + CHUNK, end)
            sink.write(generate(ctx, cursor, window_end))
            cursor = window_end
        truth = {"scenarios": ctx.truth.scenarios, "events": ctx.truth.events}
        (args.out_dir / "ground_truth.json").write_text(
            json.dumps(truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
    except (ValueError, CatalogError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2

    total = sum(sink.counts.values())
    sys.stdout.write(
        f"Generated {total} events over {args.duration_minutes} min "
        f"(bucket {BUCKET_SECONDS}s, seed {args.seed}) into {args.out_dir}\n"
    )
    for topic, count in sorted(sink.counts.items()):
        sys.stdout.write(f"  {topic:<26} {count:>8}\n")
    return 0
