"""Command line: register the schemas, publish synthetic events to Kafka.

Usage:
    python -m telecomx_generators.publish register
    python -m telecomx_generators.publish run --duration-minutes 60 --scenario all
    python -m telecomx_generators.publish run --duration-minutes 30 --poison-fraction 0.01

``run`` registers the schemas first (idempotent), then publishes the events generated for
the period, in order of arrival. Exit codes: 0 success, 1 delivery failure, 2 invalid
arguments or an unreachable registry.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import timedelta
from typing import Any

from confluent_kafka import Producer

from telecom_common.contracts.generated import BY_TOPIC
from telecom_common.contracts.manifest import load_manifest
from telecom_common.observability.logs import configure_logging
from telecom_common.timeutils import parse_utc
from telecomx_generators.events.cli import CHUNK, DEFAULT_START, _scenarios
from telecomx_generators.events.config import GeneratorConfig
from telecomx_generators.events.context import GenContext
from telecomx_generators.events.engine import generate
from telecomx_generators.publish.config import PublisherConfig, producer_settings
from telecomx_generators.publish.poison import corrupt, pick
from telecomx_generators.publish.publisher import EventPublisher, PublishError
from telecomx_generators.publish.registry import RegistryError, SchemaRegistryClient
from telecomx_generators.reference.build import DEFAULT_SEED, build_catalog

__all__ = ["main"]


def _register(publisher: EventPublisher) -> None:
    ids = publisher.register_schemas()
    sys.stdout.write(f"Registered {len(ids)} subjects\n")
    for subject, schema_id in sorted(ids.items()):
        sys.stdout.write(f"  {subject:<40} id {schema_id}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish synthetic TelecomX events to Kafka.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("register", help="Register every Avro contract in the Schema Registry.")
    run = sub.add_parser("run", help="Generate and publish events.")
    run.add_argument("--start", default=DEFAULT_START.isoformat(), help="UTC start (ISO 8601)")
    run.add_argument("--duration-minutes", type=int, default=60)
    run.add_argument("--seed", type=int, default=DEFAULT_SEED)
    run.add_argument("--speed-factor", type=float, default=GeneratorConfig().speed_factor)
    run.add_argument("--scenario", choices=["none", "degradation", "fraud", "all"], default="all")
    run.add_argument(
        "--poison-fraction",
        type=float,
        default=0.0,
        help="Share of events deliberately corrupted, to exercise the dead-letter queues.",
    )
    args = parser.parse_args(argv)

    configure_logging()
    config = PublisherConfig()
    registry = SchemaRegistryClient(config.schema_registry_url)
    try:
        registry.ping()
    except RegistryError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2

    producer = (
        Producer(
            producer_settings(
                config.kafka_bootstrap_servers, address_family=config.kafka_address_family
            )
        )
        if args.command == "run"
        else None
    )
    publisher = EventPublisher(producer, registry)
    try:
        _register(publisher)
        if args.command == "register":
            return 0

        if not 0.0 <= args.poison_fraction <= 1.0:
            sys.stderr.write("ERROR: --poison-fraction must be between 0 and 1\n")
            return 2
        start = parse_utc(args.start)
        duration = timedelta(minutes=args.duration_minutes)
        if duration <= timedelta(0):
            sys.stderr.write("ERROR: --duration-minutes must be positive\n")
            return 2
        gen_config = GeneratorConfig(seed=args.seed, speed_factor=args.speed_factor)
        catalog = build_catalog(args.seed)
        tenant_id = next(t.tenant_id for t in catalog.tenants)
        ctx = GenContext(catalog, tenant_id, gen_config, _scenarios(args.scenario, start, duration))

        specs = {s.topic: s for s in load_manifest().values()}
        poison_rng = random.Random(args.seed)  # noqa: S311 - reproducible corruption of synthetic data
        cursor, end = start, start + duration
        while cursor < end:
            window_end = min(cursor + CHUNK, end)
            for item in generate(ctx, cursor, window_end):
                event: Any = item.event
                if args.poison_fraction and poison_rng.random() < args.poison_fraction:
                    spec = specs[item.topic]
                    event = corrupt(spec, event.model_dump(), pick(spec, poison_rng), poison_rng)
                publisher.publish(item.topic, event)
            cursor = window_end
        publisher.flush()
    except (RegistryError, PublishError, ValueError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1 if isinstance(exc, PublishError) else 2

    stats = publisher.stats
    sys.stdout.write(
        f"Published {stats.total_published} events, "
        f"{stats.total_dead_lettered} dead-lettered "
        f"({stats.dead_letter_rate:.3%}; SLO < 0.1 %)\n"
    )
    for topic in sorted(BY_TOPIC):
        sys.stdout.write(f"  {topic:<26} {stats.published.get(topic, 0):>8}\n")
    for (topic, code), count in sorted(stats.dead_lettered.items()):
        sys.stdout.write(f"  DLQ {topic} {code}: {count}\n")
    return 0
