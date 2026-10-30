"""Publishes events to Kafka: validate, serialise against the registered schema, key, send.

The guarantees come from the layers around this class, and are worth stating together:

* **Conformity is checked before sending** (FR-M1-008). Anything that fails validation
  goes to ``<topic>.dlq`` with its reason instead of blocking the stream or being dropped.
* **At-least-once delivery** (FR-M1-007): the producer is idempotent with ``acks=all``
  (see :func:`producer_settings`), and :meth:`EventPublisher.flush` raises if any message
  was not acknowledged, so a failure cannot pass unnoticed.
* **Deterministic partition key** (FR-M1-007): the contract names the key field; the same
  entity always lands on the same partition, which keeps its events ordered.

One deliberate choice: messages are sent with the *publication* time as Kafka timestamp,
not the event time. Broker retention is computed from that timestamp, and a replay of last
month's events would otherwise be deleted by the retention policy the moment it is written.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from telecom_common.contracts.generated import MODELS
from telecom_common.contracts.manifest import load_manifest
from telecom_common.contracts.spec import ContractSpec
from telecom_common.observability.logs import get_logger
from telecom_common.observability.metrics import PlatformMetrics, get_metrics
from telecom_common.timeutils import utc_now
from telecomx_generators.publish.codec import AvroCodec, CodecError, frame, to_avro_record
from telecomx_generators.publish.config import DEFAULT_SCHEMA_DIR
from telecomx_generators.publish.dlq import (
    Rejection,
    RejectionCode,
    classify,
    dead_letter_record,
    dead_letter_topic,
)
from telecomx_generators.publish.registry import SchemaRegistryClient, subject_for

__all__ = ["DEAD_LETTER_CONTRACT", "EventPublisher", "ProducerLike", "PublishError", "PublishStats"]

log = get_logger(__name__)

#: File name of the dead-letter contract, under ``<schema_dir>/dlq/``.
DEAD_LETTER_CONTRACT = "dead-letter-v1.avsc"

#: Attempts to hand a message to a full local queue before giving up.
_QUEUE_RETRIES = 50


class PublishError(RuntimeError):
    """Messages could not be delivered, or the publisher is not ready to send."""


class ProducerLike(Protocol):
    """The part of ``confluent_kafka.Producer`` the publisher uses (so it can be faked)."""

    def produce(
        self,
        topic: str,
        value: bytes | None = ...,
        key: bytes | None = ...,
        *,
        on_delivery: Callable[[Any, Any], None] | None = ...,
    ) -> None: ...

    def poll(self, timeout: float = ...) -> int: ...

    def flush(self, timeout: float = ...) -> int: ...


@dataclass
class PublishStats:
    """What a publishing run did."""

    published: dict[str, int] = field(default_factory=dict)
    dead_lettered: dict[tuple[str, str], int] = field(default_factory=dict)
    delivery_failures: list[str] = field(default_factory=list)

    @property
    def total_published(self) -> int:
        return sum(self.published.values())

    @property
    def total_dead_lettered(self) -> int:
        return sum(self.dead_lettered.values())

    @property
    def dead_letter_rate(self) -> float:
        total = self.total_published + self.total_dead_lettered
        return self.total_dead_lettered / total if total else 0.0


class EventPublisher:
    """Publishes events of the eleven contracts, routing refusals to their DLQ."""

    def __init__(
        self,
        producer: ProducerLike | None,
        registry: SchemaRegistryClient,
        *,
        schema_dir: Path = DEFAULT_SCHEMA_DIR,
        metrics: PlatformMetrics | None = None,
        clock: Callable[[], datetime] = utc_now,
        seed: int = 0,
    ) -> None:
        self._producer = producer
        self._registry = registry
        self._schema_dir = schema_dir
        self._metrics = metrics if metrics is not None else get_metrics()
        self._clock = clock
        self._rng = random.Random(seed)  # noqa: S311 - ids of synthetic dead letters
        self._specs: dict[str, ContractSpec] = {s.topic: s for s in load_manifest().values()}
        self._codecs: dict[str, AvroCodec] = {}
        self._ids: dict[str, int] = {}
        self.stats = PublishStats()

    # -- Schema registration ----------------------------------------------------

    def _schema_text(self, topic: str) -> str:
        spec = self._specs[topic]
        return (self._schema_dir / f"{spec.contract_id}.avsc").read_text(encoding="utf-8")

    def _dead_letter_schema_text(self) -> str:
        return (self._schema_dir / "dlq" / DEAD_LETTER_CONTRACT).read_text(encoding="utf-8")

    def register_schemas(self) -> dict[str, int]:
        """Register every contract and pin its compatibility level; return subject -> id.

        Idempotent. The level declared in the contract (``telecomx.compatibility``) is set on
        the subject *before* registering, so that a later, incompatible change is refused by
        the registry itself rather than caught by convention (ADR-015).
        """
        registered: dict[str, int] = {}
        for topic, spec in sorted(self._specs.items()):
            text = self._schema_text(topic)
            subject = subject_for(topic)
            self._registry.set_compatibility(subject, spec.compatibility)
            schema_id = self._registry.register(subject, text)
            self._codecs[topic] = AvroCodec(text)
            self._ids[topic] = schema_id
            registered[subject] = schema_id

        dlq_text = self._dead_letter_schema_text()
        self._codecs["<dlq>"] = AvroCodec(dlq_text)
        for topic in sorted(self._specs):
            subject = subject_for(dead_letter_topic(topic))
            self._registry.set_compatibility(subject, "BACKWARD")
            schema_id = self._registry.register(subject, dlq_text)
            self._ids[dead_letter_topic(topic)] = schema_id
            registered[subject] = schema_id
        return registered

    # -- Publishing -------------------------------------------------------------

    def publish(self, topic: str, event: BaseModel | Mapping[str, Any]) -> RejectionCode | None:
        """Send one event. Returns ``None`` if it went to ``topic``, else why it went to the DLQ.

        ``event`` may be a contract model or a plain mapping (a message of unknown quality).
        Either way it is validated first.
        """
        if topic not in self._specs:
            msg = f"unknown topic {topic!r}"
            raise PublishError(msg)
        if topic not in self._ids:
            msg = "schemas are not registered: call register_schemas() first"
            raise PublishError(msg)

        payload = event.model_dump() if isinstance(event, BaseModel) else dict(event)
        key = self._key(topic, payload)

        try:
            model = MODELS[self._specs[topic].contract_id].model_validate(payload)
        except ValidationError as exc:
            rejection = classify(exc)
            self._dead_letter(topic, payload, key, rejection)
            return rejection.code

        try:
            value = frame(
                self._ids[topic], self._codecs[topic].encode(to_avro_record(model.model_dump()))
            )
        except CodecError as exc:
            rejection = Rejection(RejectionCode.SERIALIZATION_ERROR, str(exc))
            self._dead_letter(topic, payload, key, rejection)
            return rejection.code

        tenant = str(payload.get("tenant_id", "unknown"))
        self._send(topic, value, key, on_success=lambda: self._count_published(topic, tenant))
        return None

    def _key(self, topic: str, payload: Mapping[str, Any]) -> bytes | None:
        value = payload.get(self._specs[topic].partition_key)
        return str(value).encode("utf-8") if value is not None else None

    def _dead_letter(
        self, topic: str, payload: Mapping[str, Any], key: bytes | None, rejection: Rejection
    ) -> None:
        dlq_topic = dead_letter_topic(topic)
        record = dead_letter_record(
            source_topic=topic,
            rejection=rejection,
            payload=payload,
            key=key,
            now=self._clock(),
            rng=self._rng,
        )
        value = frame(self._ids[dlq_topic], self._codecs["<dlq>"].encode(record))
        self._send(dlq_topic, value, key, on_success=lambda: None)
        counter = (topic, rejection.code.value)
        self.stats.dead_lettered[counter] = self.stats.dead_lettered.get(counter, 0) + 1
        self._metrics.dlq_messages.labels(topic=topic, reason=rejection.code.value).inc()
        # Field names and error types only: never the refused values.
        log.warning(
            "event routed to dead-letter queue", extra={"topic": topic, "reason": rejection.reason}
        )

    def _send(
        self, topic: str, value: bytes, key: bytes | None, *, on_success: Callable[[], None]
    ) -> None:
        def on_delivery(error: Any, _message: Any) -> None:
            if error is not None:
                self.stats.delivery_failures.append(f"{topic}: {error}")
            else:
                on_success()

        producer = self._producer
        if producer is None:
            msg = "this publisher was built without a producer: it can only register schemas"
            raise PublishError(msg)
        for _ in range(_QUEUE_RETRIES):
            try:
                producer.produce(topic, value=value, key=key, on_delivery=on_delivery)
                break
            except BufferError:
                # The local queue is full: let delivery reports drain it, then retry.
                producer.poll(0.2)
        else:
            self.stats.delivery_failures.append(f"{topic}: local queue stayed full")
            return
        producer.poll(0)

    def _count_published(self, topic: str, tenant: str) -> None:
        self.stats.published[topic] = self.stats.published.get(topic, 0) + 1
        self._metrics.events_produced.labels(topic=topic, tenant_id=tenant).inc()

    def flush(self, timeout: float = 60.0) -> None:
        """Wait for every acknowledgement, then raise if anything failed.

        Raises:
            PublishError: if messages were not acknowledged in ``timeout`` seconds or a
                delivery was reported as failed. Nothing is ever lost silently.
        """
        remaining = self._producer.flush(timeout) if self._producer is not None else 0
        if remaining or self.stats.delivery_failures:
            first = self.stats.delivery_failures[:3]
            msg = (
                f"{remaining} message(s) still unacknowledged, "
                f"{len(self.stats.delivery_failures)} delivery failure(s): {first}"
            )
            raise PublishError(msg)
