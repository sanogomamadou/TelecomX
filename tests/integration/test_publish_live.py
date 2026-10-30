"""The publisher against the real Kafka broker and Schema Registry (``make up-core``).

Run with ``pytest -m integration`` (``make test-integration``). They are excluded from a bare
``pytest`` because they need Docker; a failure here with the stack up is a real failure.

Isolation: the topics are the platform's real ones, so every message these tests send carries a
unique ``producer_id`` and they only look at what carries it. Registry subjects created for the
compatibility test are removed afterwards.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition

from telecom_common.contracts.generated import MODELS
from telecom_common.contracts.manifest import load_manifest
from telecomx_generators.events import GenContext, GeneratorConfig, generate
from telecomx_generators.publish.codec import AvroCodec, unframe
from telecomx_generators.publish.config import PublisherConfig, producer_settings
from telecomx_generators.publish.poison import Poison, corrupt
from telecomx_generators.publish.publisher import EventPublisher
from telecomx_generators.publish.registry import (
    RegistryError,
    SchemaRegistryClient,
    subject_for,
)
from telecomx_generators.reference.build import DEFAULT_SEED, build_catalog

pytestmark = pytest.mark.integration

SPECS = {spec.topic: spec for spec in load_manifest().values()}
WAIT_SECONDS = 30


@pytest.fixture(scope="module")
def config() -> PublisherConfig:
    return PublisherConfig()


@pytest.fixture(scope="module")
def registry(config: PublisherConfig) -> SchemaRegistryClient:
    client = SchemaRegistryClient(config.schema_registry_url)
    try:
        client.ping()
    except RegistryError as exc:  # a real failure, not a skip: the stack must be up
        pytest.fail(f"Schema Registry is not reachable ({exc}). Start it with: make up-core")
    return client


@pytest.fixture
def publisher(config: PublisherConfig, registry: SchemaRegistryClient) -> EventPublisher:
    producer = Producer(
        producer_settings(
            config.kafka_bootstrap_servers, address_family=config.kafka_address_family
        )
    )
    instance = EventPublisher(producer, registry)
    instance.register_schemas()
    return instance


@pytest.fixture(scope="module")
def events() -> list[Any]:
    catalog = build_catalog(DEFAULT_SEED)
    ctx = GenContext(catalog, catalog.tenants[0].tenant_id, GeneratorConfig(), ())
    start = datetime(2026, 9, 1, 17, 30, tzinfo=UTC)
    return [
        e for e in generate(ctx, start, start + timedelta(minutes=10)) if e.topic == "cdr.events"
    ]


def _consumer(config: PublisherConfig) -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": config.kafka_bootstrap_servers,
            "broker.address.family": config.kafka_address_family,
            "group.id": f"it-{uuid.uuid4()}",
            "enable.auto.commit": False,
            "isolation.level": "read_committed",
        }
    )


def _end_offsets(config: PublisherConfig, topic: str) -> list[TopicPartition]:
    probe = _consumer(config)
    try:
        partitions = probe.list_topics(topic, timeout=15).topics[topic].partitions
        return [
            TopicPartition(
                topic, p, probe.get_watermark_offsets(TopicPartition(topic, p), timeout=15)[1]
            )
            for p in partitions
        ]
    finally:
        probe.close()


def _read(
    config: PublisherConfig,
    registry: SchemaRegistryClient,
    topic: str,
    since: list[TopicPartition],
    producer_id: str,
    expected: int,
) -> list[tuple[Any, dict[str, Any]]]:
    """Messages of ``topic`` published after ``since`` that carry ``producer_id``, decoded."""
    subject = subject_for(topic)
    consumer = _consumer(config)
    codecs: dict[int, AvroCodec] = {}
    found: list[tuple[Any, dict[str, Any]]] = []
    try:
        consumer.assign(since)
        deadline = time.monotonic() + WAIT_SECONDS
        while len(found) < expected and time.monotonic() < deadline:
            message = consumer.poll(1.0)
            if message is None or message.error():
                continue
            schema_id, payload = unframe(message.value())
            if schema_id not in codecs:
                codecs[schema_id] = AvroCodec(registry.latest(subject)["schema"])
            record = codecs[schema_id].decode(payload)
            if record.get("producer_id") == producer_id:
                found.append((message, record))
    finally:
        consumer.close()
    return found


def _mine(events: list[Any], producer_id: str, count: int) -> list[Any]:
    return [e.event.model_copy(update={"producer_id": producer_id}) for e in events[:count]]


class TestEventsReachKafka:
    def test_published_events_are_readable_and_identical(
        self,
        config: PublisherConfig,
        registry: SchemaRegistryClient,
        publisher: EventPublisher,
        events: list[Any],
    ) -> None:
        pid = f"it-{uuid.uuid4()}"
        sent = _mine(events, pid, 30)
        before = _end_offsets(config, "cdr.events")
        for event in sent:
            assert publisher.publish("cdr.events", event) is None
        publisher.flush()

        received = _read(config, registry, "cdr.events", before, pid, len(sent))
        assert len(received) == len(sent), "every acknowledged event must be readable"
        model = MODELS[SPECS["cdr.events"].contract_id]
        assert sorted(
            (model.model_validate(r) for _, r in received), key=lambda e: e.event_id
        ) == sorted(sent, key=lambda e: e.event_id)

    def test_the_wire_format_points_at_the_registered_schema(
        self,
        config: PublisherConfig,
        registry: SchemaRegistryClient,
        publisher: EventPublisher,
        events: list[Any],
    ) -> None:
        pid = f"it-{uuid.uuid4()}"
        before = _end_offsets(config, "cdr.events")
        publisher.publish("cdr.events", _mine(events, pid, 1)[0])
        publisher.flush()
        ((message, _),) = _read(config, registry, "cdr.events", before, pid, 1)
        assert unframe(message.value())[0] == registry.latest("cdr.events-value")["id"]

    def test_one_caller_always_lands_on_one_partition(
        self,
        config: PublisherConfig,
        registry: SchemaRegistryClient,
        publisher: EventPublisher,
        events: list[Any],
    ) -> None:
        pid = f"it-{uuid.uuid4()}"
        sent = _mine(events, pid, 60)
        before = _end_offsets(config, "cdr.events")
        for event in sent:
            publisher.publish("cdr.events", event)
        publisher.flush()
        received = _read(config, registry, "cdr.events", before, pid, len(sent))
        partitions: dict[bytes, set[int]] = {}
        for message, _ in received:
            partitions.setdefault(message.key(), set()).add(message.partition())
        assert len(received) == len(sent)
        assert len(partitions) > 1
        assert all(len(p) == 1 for p in partitions.values())


class TestDeadLetterLive:
    def test_a_corrupted_event_reaches_the_dlq_and_never_the_main_topic(
        self,
        config: PublisherConfig,
        registry: SchemaRegistryClient,
        publisher: EventPublisher,
        events: list[Any],
    ) -> None:
        pid = f"it-{uuid.uuid4()}"
        good = _mine(events, pid, 3)
        spec = SPECS["cdr.events"]
        main_before = _end_offsets(config, "cdr.events")
        dlq_before = _end_offsets(config, "cdr.events.dlq")
        for event, kind in zip(
            good, (Poison.MISSING_TENANT, Poison.OUT_OF_RANGE, Poison.UNKNOWN_SYMBOL), strict=True
        ):
            publisher.publish(
                "cdr.events", corrupt(spec, event.model_dump(), kind, random.Random(1))
            )
        publisher.flush()

        dead = _read(config, registry, "cdr.events.dlq", dlq_before, pid, 3)
        assert sorted(r["rejection_code"] for _, r in dead) == [
            "CONSTRAINT_VIOLATION",
            "MISSING_TENANT",
            "SCHEMA_VIOLATION",
        ]
        for _, record in dead:
            assert record["source_topic"] == "cdr.events"
            assert json.loads(record["raw_payload"])["call_id"]
        assert _read(config, registry, "cdr.events", main_before, pid, 1) == []


class TestRegistryLive:
    @pytest.fixture
    def scratch_subject(self, registry: SchemaRegistryClient) -> Iterator[str]:
        subject = f"it-{uuid.uuid4().hex[:8]}-value"
        yield subject
        registry.delete_subject(subject)

    def test_an_incompatible_evolution_is_refused_by_the_registry_itself(
        self, registry: SchemaRegistryClient, scratch_subject: str
    ) -> None:
        v1 = json.dumps({"type": "record", "name": "T", "fields": [{"name": "a", "type": "int"}]})
        required = json.dumps(
            {
                "type": "record",
                "name": "T",
                "fields": [{"name": "a", "type": "int"}, {"name": "b", "type": "string"}],
            }
        )
        optional = json.dumps(
            {
                "type": "record",
                "name": "T",
                "fields": [
                    {"name": "a", "type": "int"},
                    {"name": "b", "type": "string", "default": ""},
                ],
            }
        )
        registry.set_compatibility(scratch_subject, "BACKWARD")
        registry.register(scratch_subject, v1)

        assert registry.is_compatible(scratch_subject, required) is False
        with pytest.raises(RegistryError) as refused:
            registry.register(scratch_subject, required)
        assert refused.value.status == 409

        assert registry.is_compatible(scratch_subject, optional) is True
        registry.register(scratch_subject, optional)

    def test_every_contract_has_its_declared_compatibility_pinned(
        self, registry: SchemaRegistryClient, publisher: EventPublisher
    ) -> None:
        for topic, spec in SPECS.items():
            assert registry.get_compatibility(subject_for(topic)) == spec.compatibility


class TestBrokerLive:
    def test_a_topic_that_was_not_declared_is_not_created_on_the_fly(
        self, config: PublisherConfig
    ) -> None:
        """A typo in a topic name must be an error, not a new, empty topic (ADR-020)."""
        errors: list[KafkaError] = []
        producer = Producer(
            {**producer_settings(config.kafka_bootstrap_servers), "message.timeout.ms": 8000}
        )
        producer.produce(
            f"it.undeclared.{uuid.uuid4().hex[:8]}",
            value=b"x",
            on_delivery=lambda err, _msg: errors.append(err) if err else None,
        )
        producer.flush(15)
        assert errors, "the broker accepted a message for an undeclared topic"
        assert errors[0].code() in {KafkaError.UNKNOWN_TOPIC_OR_PART, KafkaError._MSG_TIMED_OUT}
