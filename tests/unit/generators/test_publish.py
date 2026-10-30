"""Tests for the Kafka publisher (lot 1.6): codec, registry client, dead-letter routing.

Nothing here needs Docker. The registry is a small HTTP server running in this process, and the
producer is a fake that records what it is asked to send. The tests that use the real broker
and registry are in ``tests/integration/test_publish_live.py``.
"""

from __future__ import annotations

import json
import random
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from prometheus_client import CollectorRegistry

from telecom_common.contracts.generated import BY_TOPIC, MODELS
from telecom_common.contracts.manifest import load_manifest
from telecom_common.observability.metrics import PlatformMetrics
from telecomx_generators.events import GenContext, GeneratorConfig, generate
from telecomx_generators.events.engine import Emitted
from telecomx_generators.publish import cli
from telecomx_generators.publish.codec import (
    AvroCodec,
    CodecError,
    frame,
    to_avro_record,
    unframe,
)
from telecomx_generators.publish.config import (
    DEFAULT_SCHEMA_DIR,
    PublisherConfig,
    producer_settings,
)
from telecomx_generators.publish.dlq import (
    Rejection,
    RejectionCode,
    dead_letter_record,
    dead_letter_topic,
)
from telecomx_generators.publish.poison import Poison, applicable, corrupt, pick
from telecomx_generators.publish.publisher import (
    DEAD_LETTER_CONTRACT,
    EventPublisher,
    PublishError,
)
from telecomx_generators.publish.registry import RegistryError, SchemaRegistryClient, subject_for
from telecomx_generators.reference.build import DEFAULT_SEED, build_catalog

pytestmark = pytest.mark.unit

START = datetime(2026, 9, 1, 17, 30, tzinfo=UTC)
SPECS = {spec.topic: spec for spec in load_manifest().values()}


# -- Test doubles ---------------------------------------------------------------------


class FakeRegistry:
    """Stands in for :class:`SchemaRegistryClient` when the HTTP layer is not under test."""

    def __init__(self) -> None:
        self.ids: dict[str, int] = {}
        self.levels: dict[str, str] = {}
        self._by_schema: dict[str, int] = {}

    def set_compatibility(self, subject: str, level: str) -> None:
        self.levels[subject] = level

    def register(self, subject: str, schema_text: str) -> int:
        schema_id = self._by_schema.setdefault(schema_text, len(self._by_schema) + 1)
        self.ids[subject] = schema_id
        return schema_id


class FakeProducer:
    """Records produced messages; delivery reports fire on ``poll`` / ``flush`` like the real one."""

    def __init__(
        self,
        *,
        fail_delivery: bool = False,
        full_queue: int = 0,
        unacked: int = 0,
        defer_reports: bool = False,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self._pending: list[Callable[[], None]] = []
        self._fail = fail_delivery
        self._full = full_queue
        self._unacked = unacked
        self._defer = defer_reports
        self.polls = 0

    def produce(
        self,
        topic: str,
        value: bytes | None = None,
        key: bytes | None = None,
        *,
        on_delivery: Callable[[Any, Any], None] | None = None,
        **extra: Any,
    ) -> None:
        if self._full > 0:
            self._full -= 1
            raise BufferError("queue full")
        self.sent.append({"topic": topic, "value": value, "key": key, **extra})
        if on_delivery is not None:
            error = RuntimeError("broker said no") if self._fail else None
            self._pending.append(lambda: on_delivery(error, None))

    def poll(self, timeout: float = 0) -> int:
        self.polls += 1
        return 0 if self._defer else self._drain()

    def flush(self, timeout: float = 0) -> int:
        self._drain()
        return self._unacked

    def _drain(self) -> int:
        callbacks, self._pending = self._pending, []
        for callback in callbacks:
            callback()
        return len(callbacks)

    def on(self, topic: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m["topic"] == topic]


class _RegistryHandler(BaseHTTPRequestHandler):
    """A tiny Schema Registry: enough of the REST API to test the client for real."""

    state: dict[str, Any]

    def log_message(self, *args: object) -> None:  # silence the test output
        pass

    def _reply(self, status: int, body: object) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/vnd.schemaregistry.v1+json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")  # type: ignore[no-any-return]

    def do_GET(self) -> None:
        state = self.state
        state["requests"].append(("GET", self.path))
        if self.path == "/subjects":
            self._reply(200, sorted(state["subjects"]))
        elif self.path.endswith("/versions/latest"):
            subject = self.path.split("/")[2]
            if subject in state["subjects"]:
                self._reply(
                    200, {"id": state["subjects"][subject]["id"], "version": 1, "schema": "{}"}
                )
            else:
                self._reply(404, {"error_code": 40401, "message": "Subject not found."})
        elif self.path.startswith("/config/"):
            subject = self.path.split("/")[2].split("?")[0]
            level = state["levels"].get(subject)
            if level:
                self._reply(200, {"compatibilityLevel": level})
            else:
                self._reply(404, {"error_code": 40408, "message": "Subject config not found."})
        else:
            self._reply(500, {"message": "unexpected"})

    def do_POST(self) -> None:
        state = self.state
        body = self._body()
        state["requests"].append(("POST", self.path))
        if self.path.startswith("/compatibility/"):
            subject = self.path.split("/")[3]
            if subject not in state["subjects"]:
                self._reply(404, {"error_code": 40401, "message": "Subject not found."})
            else:
                self._reply(
                    200, {"is_compatible": body["schema"] == state["subjects"][subject]["schema"]}
                )
        elif self.path.endswith("/versions"):
            subject = self.path.split("/")[2]
            known = state["subjects"].get(subject)
            if (
                known
                and known["schema"] != body["schema"]
                and state["levels"].get(subject) != "NONE"
            ):
                self._reply(
                    409, {"error_code": 409, "message": "Schema being registered is incompatible"}
                )
                return
            schema_id = known["id"] if known else len(state["subjects"]) + 100
            state["subjects"][subject] = {"id": schema_id, "schema": body["schema"]}
            self._reply(200, {"id": schema_id})
        else:
            self._reply(500, {"message": "unexpected"})

    def do_DELETE(self) -> None:
        subject = self.path.split("/")[2].split("?")[0]
        self.state["requests"].append(("DELETE", self.path))
        if "permanent=true" in self.path:
            self.state["subjects"].pop(subject, None)
        self._reply(200, [1])

    def do_PUT(self) -> None:
        subject = self.path.split("/")[2]
        self.state["requests"].append(("PUT", self.path))
        self.state["levels"][subject] = self._body()["compatibility"]
        self._reply(200, {"compatibility": self.state["levels"][subject]})


@pytest.fixture
def registry_server() -> Iterator[tuple[str, dict[str, Any]]]:
    state: dict[str, Any] = {"subjects": {}, "levels": {}, "requests": []}
    handler = type("Handler", (_RegistryHandler,), {"state": state})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()


class _BrokenHandler(BaseHTTPRequestHandler):
    """A registry that answers every request with a plain-text 500."""

    def log_message(self, *args: object) -> None:
        pass

    def _fail(self) -> None:
        body = b"upstream exploded"
        self.send_response(500)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = _fail  # noqa: N815


@pytest.fixture
def broken_registry() -> Iterator[SchemaRegistryClient]:
    server = HTTPServer(("127.0.0.1", 0), _BrokenHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield SchemaRegistryClient(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()


# -- Shared fixtures ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def emitted() -> list[Emitted]:
    """A few minutes of real generated events, every topic represented."""
    catalog = build_catalog(DEFAULT_SEED)
    ctx = GenContext(catalog, catalog.tenants[0].tenant_id, GeneratorConfig(), ())
    return generate(ctx, START, START + timedelta(minutes=12))


def _one_per_topic(items: list[Emitted]) -> dict[str, Any]:
    first: dict[str, Any] = {}
    for item in items:
        first.setdefault(item.topic, item.event)
    return first


@pytest.fixture
def metrics() -> PlatformMetrics:
    return PlatformMetrics(CollectorRegistry())


def _publisher(
    producer: FakeProducer | None, metrics: PlatformMetrics, registry: FakeRegistry | None = None
) -> EventPublisher:
    publisher = EventPublisher(
        producer,
        registry or FakeRegistry(),  # type: ignore[arg-type]
        metrics=metrics,
        clock=lambda: START,
        seed=1,
    )
    publisher.register_schemas()
    return publisher


def _decode(topic: str, message: dict[str, Any]) -> dict[str, Any]:
    schema_id, payload = unframe(message["value"])
    assert schema_id > 0
    path = (
        DEFAULT_SCHEMA_DIR / "dlq" / DEAD_LETTER_CONTRACT
        if topic.endswith(".dlq")
        else DEFAULT_SCHEMA_DIR / f"{SPECS[topic].contract_id}.avsc"
    )
    return AvroCodec(path.read_text(encoding="utf-8")).decode(payload)


# -- Wire format and codec -----------------------------------------------------------------


class TestWireFormat:
    def test_frame_and_unframe_are_inverse(self) -> None:
        assert unframe(frame(258, b"abc")) == (258, b"abc")

    def test_the_header_is_magic_byte_then_big_endian_schema_id(self) -> None:
        assert frame(1, b"x")[:5] == b"\x00\x00\x00\x00\x01"

    @pytest.mark.parametrize("message", [b"", b"\x00\x00", b"\x01\x00\x00\x00\x01rest"])
    def test_a_message_without_the_header_is_refused(self, message: bytes) -> None:
        with pytest.raises(CodecError):
            unframe(message)


class TestCodec:
    @pytest.mark.parametrize("topic", sorted(BY_TOPIC))
    def test_every_generated_event_survives_an_avro_round_trip(
        self, emitted: list[Emitted], topic: str
    ) -> None:
        events = [e.event for e in emitted if e.topic == topic][:40]
        assert events, topic
        codec = AvroCodec(
            (DEFAULT_SCHEMA_DIR / f"{SPECS[topic].contract_id}.avsc").read_text("utf-8")
        )
        for event in events:
            decoded = codec.decode(codec.encode(to_avro_record(event.model_dump())))
            assert MODELS[SPECS[topic].contract_id].model_validate(decoded) == event

    def test_money_keeps_its_two_decimals(self, emitted: list[Emitted]) -> None:
        event = next(e.event for e in emitted if e.topic == "mobilemoney.transactions")
        codec = AvroCodec(
            (DEFAULT_SCHEMA_DIR / "mobilemoney-transaction-v1.avsc").read_text("utf-8")
        )
        decoded = codec.decode(codec.encode(to_avro_record(event.model_dump())))
        assert decoded["amount"] == event.amount
        assert isinstance(decoded["amount"], Decimal)

    def test_timestamps_keep_microseconds_and_utc(self, emitted: list[Emitted]) -> None:
        event = emitted[0].event
        codec = AvroCodec(
            (DEFAULT_SCHEMA_DIR / f"{SPECS[emitted[0].topic].contract_id}.avsc").read_text("utf-8")
        )
        decoded = codec.decode(codec.encode(to_avro_record(event.model_dump())))
        assert decoded["event_timestamp"] == event.event_timestamp
        assert decoded["event_timestamp"].utcoffset() == timedelta(0)

    def test_a_record_missing_a_field_cannot_be_encoded(self, emitted: list[Emitted]) -> None:
        item = next(e for e in emitted if e.topic == "sms.events")
        codec = AvroCodec((DEFAULT_SCHEMA_DIR / "sms-v1.avsc").read_text("utf-8"))
        record = to_avro_record(item.event.model_dump())
        del record["sender_id"]
        with pytest.raises(CodecError):
            codec.encode(record)

    def test_a_schema_that_is_not_a_record_cannot_be_decoded_as_one(self) -> None:
        codec = AvroCodec('{"type": "string"}')
        with pytest.raises(CodecError, match="did not decode to a record"):
            codec.decode(b"ab")  # Avro string "ab"

    def test_an_invalid_schema_is_refused(self) -> None:
        with pytest.raises(CodecError):
            AvroCodec('{"type": "record"}')
        with pytest.raises(CodecError):
            AvroCodec("not json")

    def test_a_payload_from_another_schema_is_refused(self, emitted: list[Emitted]) -> None:
        item = next(e for e in emitted if e.topic == "sms.events")
        sms = AvroCodec((DEFAULT_SCHEMA_DIR / "sms-v1.avsc").read_text("utf-8"))
        payload = sms.encode(to_avro_record(item.event.model_dump()))
        with pytest.raises(CodecError):
            AvroCodec((DEFAULT_SCHEMA_DIR / "cdr-v1.avsc").read_text("utf-8")).decode(payload[:5])


# -- Schema Registry client ---------------------------------------------------------------


class TestRegistryClient:
    def test_register_returns_an_id_and_is_idempotent(
        self, registry_server: tuple[str, dict]
    ) -> None:
        url, _ = registry_server
        client = SchemaRegistryClient(url)
        first = client.register("a-value", '{"type":"string"}')
        assert client.register("a-value", '{"type":"string"}') == first

    def test_an_incompatible_change_is_refused_with_status_409(
        self, registry_server: tuple[str, dict]
    ) -> None:
        client = SchemaRegistryClient(registry_server[0])
        client.register("a-value", '{"type":"string"}')
        with pytest.raises(RegistryError) as raised:
            client.register("a-value", '{"type":"long"}')
        assert raised.value.status == 409
        assert "incompatible" in str(raised.value)

    def test_compatibility_check_of_a_new_subject_is_true(
        self, registry_server: tuple[str, dict]
    ) -> None:
        assert SchemaRegistryClient(registry_server[0]).is_compatible("fresh-value", "{}") is True

    def test_compatibility_check_of_a_changed_schema_is_false(
        self, registry_server: tuple[str, dict]
    ) -> None:
        client = SchemaRegistryClient(registry_server[0])
        client.register("a-value", "A")
        assert client.is_compatible("a-value", "A") is True
        assert client.is_compatible("a-value", "B") is False

    def test_compatibility_level_round_trip(self, registry_server: tuple[str, dict]) -> None:
        client = SchemaRegistryClient(registry_server[0])
        assert client.get_compatibility("a-value") is None
        client.set_compatibility("a-value", "FULL")
        assert client.get_compatibility("a-value") == "FULL"

    def test_an_unknown_level_is_refused_before_any_request(
        self, registry_server: tuple[str, dict]
    ) -> None:
        with pytest.raises(ValueError, match="unknown compatibility"):
            SchemaRegistryClient(registry_server[0]).set_compatibility("a-value", "SOMETIMES")
        assert registry_server[1]["requests"] == []

    def test_subjects_and_latest(self, registry_server: tuple[str, dict]) -> None:
        client = SchemaRegistryClient(registry_server[0])
        client.register("b-value", "S")
        assert client.subjects() == ["b-value"]
        assert client.latest("b-value")["version"] == 1
        with pytest.raises(RegistryError) as raised:
            client.latest("missing-value")
        assert raised.value.status == 404

    def test_delete_subject_is_soft_then_permanent(self, registry_server: tuple[str, dict]) -> None:
        client = SchemaRegistryClient(registry_server[0])
        client.register("tmp-value", "S")
        client.delete_subject("tmp-value")
        assert client.subjects() == []
        deletes = [r for r in registry_server[1]["requests"] if r[0] == "DELETE"]
        assert [path for _, path in deletes] == [
            "/subjects/tmp-value",
            "/subjects/tmp-value?permanent=true",
        ]

    def test_subjects_with_dots_are_addressed_correctly(
        self, registry_server: tuple[str, dict]
    ) -> None:
        client = SchemaRegistryClient(registry_server[0])
        client.register(subject_for("cdr.events.dlq"), "S")
        assert client.subjects() == ["cdr.events.dlq-value"]

    def test_a_server_error_with_a_plain_text_body_is_reported_with_its_status(
        self, broken_registry: SchemaRegistryClient
    ) -> None:
        with pytest.raises(RegistryError) as raised:
            broken_registry.register("a-value", "S")
        assert raised.value.status == 500
        assert "500" in str(raised.value)

    def test_only_a_404_means_nothing_to_compare_with(
        self, broken_registry: SchemaRegistryClient
    ) -> None:
        """Any other failure must surface: treating it as "compatible" would hide an outage."""
        with pytest.raises(RegistryError):
            broken_registry.is_compatible("a-value", "S")
        with pytest.raises(RegistryError):
            broken_registry.get_compatibility("a-value")

    def test_an_unreachable_registry_is_an_error_without_status(self) -> None:
        with pytest.raises(RegistryError) as raised:
            SchemaRegistryClient("http://127.0.0.1:9", timeout=1.0).ping()
        assert raised.value.status is None
        assert "unreachable" in str(raised.value)

    @pytest.mark.parametrize("url", ["localhost:8081", "ftp://x", ""])
    def test_a_non_http_url_is_refused(self, url: str) -> None:
        with pytest.raises(ValueError, match="http"):
            SchemaRegistryClient(url)


# -- Registration of the contracts --------------------------------------------------------


class TestRegistration:
    def test_every_contract_and_every_dead_letter_subject_is_registered(
        self, metrics: PlatformMetrics
    ) -> None:
        registry = FakeRegistry()
        _publisher(None, metrics, registry)
        expected = {subject_for(t) for t in BY_TOPIC} | {
            subject_for(dead_letter_topic(t)) for t in BY_TOPIC
        }
        assert set(registry.ids) == expected

    def test_each_subject_gets_the_compatibility_its_contract_declares(
        self, metrics: PlatformMetrics
    ) -> None:
        registry = FakeRegistry()
        _publisher(None, metrics, registry)
        for topic, spec in SPECS.items():
            assert registry.levels[subject_for(topic)] == spec.compatibility
        assert {registry.levels[subject_for(dead_letter_topic(t))] for t in SPECS} == {"BACKWARD"}

    def test_the_compatibility_level_is_pinned_before_the_schema_is_registered(
        self, registry_server: tuple[str, dict], metrics: PlatformMetrics
    ) -> None:
        """Otherwise the first version would be accepted under the registry's default."""
        url, state = registry_server
        _publisher(None, metrics, SchemaRegistryClient(url))  # type: ignore[arg-type]
        requests = state["requests"]
        put = requests.index(("PUT", "/config/cdr.events-value"))
        post = requests.index(("POST", "/subjects/cdr.events-value/versions"))
        assert put < post

    def test_registering_twice_gives_the_same_ids(
        self, registry_server: tuple[str, dict], metrics: PlatformMetrics
    ) -> None:
        client = SchemaRegistryClient(registry_server[0])
        first = _publisher(None, metrics, client).register_schemas()  # type: ignore[arg-type]
        assert _publisher(None, metrics, client).register_schemas() == first  # type: ignore[arg-type]

    def test_the_dead_letter_subjects_share_one_schema_id(self, metrics: PlatformMetrics) -> None:
        registry = FakeRegistry()
        _publisher(None, metrics, registry)
        assert len({registry.ids[subject_for(dead_letter_topic(t))] for t in SPECS}) == 1


# -- Publishing valid events ----------------------------------------------------------------


class TestPublishing:
    @pytest.mark.parametrize("topic", sorted(BY_TOPIC))
    def test_a_valid_event_goes_to_its_topic_with_its_partition_key(
        self, emitted: list[Emitted], metrics: PlatformMetrics, topic: str
    ) -> None:
        producer = FakeProducer()
        publisher = _publisher(producer, metrics)
        event = _one_per_topic(emitted)[topic]
        assert publisher.publish(topic, event) is None
        (message,) = producer.on(topic)
        expected_key = str(getattr(event, SPECS[topic].partition_key)).encode()
        assert message["key"] == expected_key
        assert MODELS[SPECS[topic].contract_id].model_validate(_decode(topic, message)) == event
        assert producer.on(dead_letter_topic(topic)) == []

    def test_the_value_starts_with_the_schema_id_of_its_subject(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        registry = FakeRegistry()
        producer = FakeProducer()
        publisher = _publisher(producer, metrics, registry)
        publisher.publish("cdr.events", _one_per_topic(emitted)["cdr.events"])
        assert unframe(producer.on("cdr.events")[0]["value"])[0] == registry.ids["cdr.events-value"]

    def test_the_same_entity_always_gets_the_same_key(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        producer = FakeProducer()
        publisher = _publisher(producer, metrics)
        calls = [e.event for e in emitted if e.topic == "cdr.events"]
        for call in calls:
            publisher.publish("cdr.events", call)
        by_caller: dict[str, set[bytes]] = {}
        for call, message in zip(calls, producer.on("cdr.events"), strict=True):
            by_caller.setdefault(call.caller_id, set()).add(message["key"])
        assert all(len(keys) == 1 for keys in by_caller.values())

    def test_kafka_timestamp_is_left_to_the_producer(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        """Event time as Kafka timestamp would let retention delete a replay as it is written."""
        producer = FakeProducer()
        _publisher(producer, metrics).publish("sms.events", _one_per_topic(emitted)["sms.events"])
        assert "timestamp" not in producer.on("sms.events")[0]

    def test_a_model_and_its_dict_are_published_identically(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        event = _one_per_topic(emitted)["ussd.events"]
        values = []
        for payload in (event, event.model_dump()):
            producer = FakeProducer()
            _publisher(producer, metrics).publish("ussd.events", payload)
            values.append(producer.on("ussd.events")[0]["value"])
        assert values[0] == values[1]

    def test_publishing_before_registration_is_refused(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        publisher = EventPublisher(FakeProducer(), FakeRegistry(), metrics=metrics)  # type: ignore[arg-type]
        with pytest.raises(PublishError, match="register_schemas"):
            publisher.publish("cdr.events", _one_per_topic(emitted)["cdr.events"])

    def test_an_unknown_topic_is_refused(self, metrics: PlatformMetrics) -> None:
        with pytest.raises(PublishError, match="unknown topic"):
            _publisher(FakeProducer(), metrics).publish("nope.events", {})

    def test_a_publisher_without_producer_can_register_but_not_send(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        publisher = _publisher(None, metrics)
        with pytest.raises(PublishError, match="without a producer"):
            publisher.publish("cdr.events", _one_per_topic(emitted)["cdr.events"])
        publisher.flush()  # nothing to wait for, nothing to raise


# -- Dead-letter routing ----------------------------------------------------------------------


EXPECTED_CODE = {
    Poison.MISSING_TENANT: RejectionCode.MISSING_TENANT,
    Poison.MISSING_FIELD: RejectionCode.SCHEMA_VIOLATION,
    Poison.UNKNOWN_SYMBOL: RejectionCode.SCHEMA_VIOLATION,
    Poison.WRONG_TYPE: RejectionCode.SCHEMA_VIOLATION,
    Poison.OUT_OF_RANGE: RejectionCode.CONSTRAINT_VIOLATION,
    Poison.FUTURE_TIMESTAMP: RejectionCode.CONSTRAINT_VIOLATION,
}

CASES = [(topic, kind) for topic in sorted(BY_TOPIC) for kind in applicable(SPECS[topic])]


class TestDeadLetter:
    def test_the_matrix_covers_every_corruption_somewhere(self) -> None:
        assert {kind for _, kind in CASES} == set(Poison)

    @pytest.mark.parametrize(("topic", "kind"), CASES, ids=[f"{t}-{k.value}" for t, k in CASES])
    def test_each_corruption_is_routed_to_the_dlq_with_the_right_code(
        self, emitted: list[Emitted], metrics: PlatformMetrics, topic: str, kind: Poison
    ) -> None:
        producer = FakeProducer()
        publisher = _publisher(producer, metrics)
        event = _one_per_topic(emitted)[topic]
        broken = corrupt(SPECS[topic], event.model_dump(), kind, random.Random(3))

        assert publisher.publish(topic, broken) is EXPECTED_CODE[kind]

        assert producer.on(topic) == [], "a refused message must never reach the main topic"
        (message,) = producer.on(dead_letter_topic(topic))
        record = _decode(dead_letter_topic(topic), message)
        assert record["rejection_code"] == EXPECTED_CODE[kind].value
        assert record["source_topic"] == topic
        assert record["producer_id"] == event.producer_id

    def test_the_dead_letter_carries_the_refused_message_for_replay(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        producer = FakeProducer()
        event = _one_per_topic(emitted)["recharge.events"]
        broken = corrupt(
            SPECS["recharge.events"], event.model_dump(), Poison.OUT_OF_RANGE, random.Random(1)
        )
        _publisher(producer, metrics).publish("recharge.events", broken)
        record = _decode("recharge.events.dlq", producer.on("recharge.events.dlq")[0])
        raw = json.loads(record["raw_payload"])
        assert raw["recharge_id"] == event.recharge_id
        assert raw["subscriber_id"] == event.subscriber_id

    def test_the_dead_letter_is_keyed_like_the_original_so_a_replay_keeps_its_partition(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        producer = FakeProducer()
        event = _one_per_topic(emitted)["cdr.events"]
        broken = corrupt(
            SPECS["cdr.events"], event.model_dump(), Poison.MISSING_TENANT, random.Random(1)
        )
        _publisher(producer, metrics).publish("cdr.events", broken)
        message = producer.on("cdr.events.dlq")[0]
        assert message["key"] == event.caller_id.encode()
        assert _decode("cdr.events.dlq", message)["source_key"] == event.caller_id.encode()

    def test_a_missing_tenant_leaves_the_tenant_null(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        producer = FakeProducer()
        event = _one_per_topic(emitted)["sms.events"]
        broken = corrupt(
            SPECS["sms.events"], event.model_dump(), Poison.MISSING_TENANT, random.Random(1)
        )
        _publisher(producer, metrics).publish("sms.events", broken)
        assert _decode("sms.events.dlq", producer.on("sms.events.dlq")[0])["tenant_id"] is None

    def test_the_reason_never_contains_a_refused_value(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        """Values are MSISDNs and IMSIs: the reason ends up in logs and alerts."""
        producer = FakeProducer()
        event = _one_per_topic(emitted)["cdr.events"]
        broken = event.model_dump()
        broken["duration_seconds"] = "SECRET-VALUE-+22300000042"
        broken["caller_id"] = event.caller_id
        _publisher(producer, metrics).publish("cdr.events", broken)
        record = _decode("cdr.events.dlq", producer.on("cdr.events.dlq")[0])
        assert "SECRET-VALUE" not in record["rejection_reason"]
        assert event.caller_id not in record["rejection_reason"]
        assert "duration_seconds" in record["rejection_reason"]

    def test_a_message_that_validates_but_cannot_be_encoded_is_a_serialization_error(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        producer = FakeProducer()
        publisher = _publisher(producer, metrics)

        def refuse(record: object) -> bytes:
            raise CodecError("simulated encoder failure")

        # Only the event's own codec fails: the dead-letter codec must keep working.
        publisher._codecs["cdr.events"].encode = refuse  # type: ignore[method-assign]
        code = publisher.publish("cdr.events", _one_per_topic(emitted)["cdr.events"])
        assert code is RejectionCode.SERIALIZATION_ERROR
        assert producer.on("cdr.events") == []
        record = _decode("cdr.events.dlq", producer.on("cdr.events.dlq")[0])
        assert record["rejection_code"] == "SERIALIZATION_ERROR"

    def test_the_stats_and_metrics_count_each_refusal(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        producer = FakeProducer()
        publisher = _publisher(producer, metrics)
        event = _one_per_topic(emitted)["ussd.events"]
        for _ in range(3):
            publisher.publish(
                "ussd.events",
                corrupt(
                    SPECS["ussd.events"],
                    event.model_dump(),
                    Poison.MISSING_TENANT,
                    random.Random(1),
                ),
            )
        publisher.publish("ussd.events", event)
        publisher.flush()
        assert publisher.stats.dead_lettered == {("ussd.events", "MISSING_TENANT"): 3}
        assert publisher.stats.dead_letter_rate == pytest.approx(0.75)
        assert (
            metrics.dlq_messages.labels(topic="ussd.events", reason="MISSING_TENANT")._value.get()
            == 3
        )

    def test_poison_pick_is_reproducible(self) -> None:
        spec = SPECS["cdr.events"]
        assert pick(spec, random.Random(5)) is pick(spec, random.Random(5))


def _validator() -> Any:
    """scripts/validate_contracts.py, loaded by path (it is a stand-alone script)."""
    import importlib.util
    import sys

    path = DEFAULT_SCHEMA_DIR.parents[2] / "scripts" / "validate_contracts.py"
    module_spec = importlib.util.spec_from_file_location("validate_contracts_dlq", path)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules["validate_contracts_dlq"] = module  # @dataclass needs the module registered
    try:
        module_spec.loader.exec_module(module)
    finally:
        sys.modules.pop("validate_contracts_dlq", None)
    return module


class TestDeadLetterValidation:
    """The standalone validator knows the dead-letter contract and holds it to its own rules."""

    def _contract(self, tmp_path: Any, mutate: Callable[[dict[str, Any]], None]) -> Any:
        schema = json.loads((DEFAULT_SCHEMA_DIR / "dlq" / DEAD_LETTER_CONTRACT).read_text("utf-8"))
        mutate(schema)
        path = tmp_path / "dead-letter-v1.avsc"
        path.write_text(json.dumps(schema), encoding="utf-8")
        return path

    def test_the_real_contract_is_valid(self) -> None:
        report = _validator().validate_contract(
            DEFAULT_SCHEMA_DIR / "dlq" / DEAD_LETTER_CONTRACT, dead_letter=True
        )
        assert report.errors == []

    def test_the_required_fields_agree_with_the_publisher(self) -> None:
        schema = json.loads((DEFAULT_SCHEMA_DIR / "dlq" / DEAD_LETTER_CONTRACT).read_text("utf-8"))
        built = dead_letter_record(
            source_topic="t",
            rejection=Rejection(RejectionCode.SCHEMA_VIOLATION, "r"),
            payload={},
            key=None,
            now=START,
            rng=random.Random(1),
        )
        assert {f["name"] for f in schema["fields"]} == set(built)
        assert set(built) >= _validator().DLQ_REQUIRED_FIELDS

    @pytest.mark.parametrize(
        "field",
        sorted(
            [
                "dead_letter_id",
                "rejected_at",
                "source_topic",
                "rejection_code",
                "rejection_reason",
                "producer_id",
                "raw_payload",
            ]
        ),
    )
    def test_a_dead_letter_contract_without_a_required_field_is_refused(
        self, tmp_path: Any, field: str
    ) -> None:
        path = self._contract(
            tmp_path, lambda s: s.update(fields=[f for f in s["fields"] if f["name"] != field])
        )
        report = _validator().validate_contract(path, dead_letter=True)
        assert any(field in error for error in report.errors)

    def test_a_wrong_topic_pattern_is_refused(self, tmp_path: Any) -> None:
        path = self._contract(tmp_path, lambda s: s.update({"telecomx.topic": "cdr.events"}))
        assert any(
            "<topic>.dlq" in e
            for e in _validator().validate_contract(path, dead_letter=True).errors
        )

    def test_an_undocumented_field_is_refused(self, tmp_path: Any) -> None:
        def strip_doc(schema: dict[str, Any]) -> None:
            schema["fields"][2].pop("doc")

        path = self._contract(tmp_path, strip_doc)
        assert any(
            "doc" in e for e in _validator().validate_contract(path, dead_letter=True).errors
        )

    def test_an_event_contract_is_still_held_to_the_event_envelope(self) -> None:
        """The dead-letter exception must not loosen the rules of the eleven event contracts."""
        report = _validator().validate_contract(DEFAULT_SCHEMA_DIR / "dlq" / DEAD_LETTER_CONTRACT)
        assert any("envelope field" in e for e in report.errors)


class TestDeadLetterContract:
    def test_the_enum_of_the_contract_and_the_code_enum_agree(self) -> None:
        schema = json.loads((DEFAULT_SCHEMA_DIR / "dlq" / DEAD_LETTER_CONTRACT).read_text("utf-8"))
        field = next(f for f in schema["fields"] if f["name"] == "rejection_code")
        assert field["type"]["symbols"] == [c.value for c in RejectionCode]

    def test_a_record_built_for_every_code_encodes_with_the_contract(self) -> None:
        codec = AvroCodec((DEFAULT_SCHEMA_DIR / "dlq" / DEAD_LETTER_CONTRACT).read_text("utf-8"))
        for code in RejectionCode:
            record = dead_letter_record(
                source_topic="cdr.events",
                rejection=Rejection(code, "caller_id: missing"),
                payload={"producer_id": "p", "tenant_id": "t", "when": START, "n": Decimal("1.50")},
                key=b"k",
                now=START,
                rng=random.Random(1),
            )
            assert codec.decode(codec.encode(record))["rejection_code"] == code.value

    def test_a_payload_without_producer_is_attributed_to_unknown(self) -> None:
        record = dead_letter_record(
            source_topic="x",
            rejection=Rejection(RejectionCode.SCHEMA_VIOLATION, "r"),
            payload={},
            key=None,
            now=START,
            rng=random.Random(1),
        )
        assert record["producer_id"] == "unknown"
        assert record["tenant_id"] is None

    def test_the_refused_message_is_stored_whatever_it_contains(self) -> None:
        """Values of any type must survive into the JSON payload, replayable or at least readable."""
        from enum import Enum

        class Colour(Enum):
            RED = "red"

        record = dead_letter_record(
            source_topic="x",
            rejection=Rejection(RejectionCode.SCHEMA_VIOLATION, "r"),
            payload={
                "when": START,
                "amount": Decimal("1.50"),
                "c": Colour.RED,
                "raw": bytes([0, 1]),
                "o": object,
            },
            key=None,
            now=START,
            rng=random.Random(1),
        )
        stored = json.loads(record["raw_payload"])
        assert stored["when"] == START.isoformat()
        assert stored["amount"] == "1.50"
        assert stored["c"] == "red"
        assert stored["raw"] == "AAE="
        assert "object" in stored["o"]

    def test_a_rejection_shows_its_code_when_printed(self) -> None:
        assert "MISSING_TENANT" in repr(
            Rejection(RejectionCode.MISSING_TENANT, "tenant_id: missing")
        )

    def test_a_very_long_reason_is_truncated(self) -> None:
        assert len(Rejection(RejectionCode.SCHEMA_VIOLATION, "x" * 5000).reason) == 1000


# -- Delivery guarantees ------------------------------------------------------------------------


class TestDelivery:
    def test_events_are_counted_published_only_once_acknowledged(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        """Counting at enqueue time would report events the broker never received."""
        event = _one_per_topic(emitted)["sms.events"]
        publisher = _publisher(FakeProducer(defer_reports=True), metrics)
        publisher.publish("sms.events", event)
        assert publisher.stats.published == {}
        publisher.flush()
        assert publisher.stats.published == {"sms.events": 1}
        tenant = event.tenant_id
        assert (
            metrics.events_produced.labels(topic="sms.events", tenant_id=tenant)._value.get() == 1
        )

    def test_a_failed_delivery_is_raised_by_flush_not_swallowed(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        publisher = _publisher(FakeProducer(fail_delivery=True), metrics)
        publisher.publish("sms.events", _one_per_topic(emitted)["sms.events"])
        with pytest.raises(PublishError, match="delivery failure"):
            publisher.flush()
        assert publisher.stats.total_published == 0

    def test_unacknowledged_messages_after_the_timeout_are_raised(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        publisher = _publisher(FakeProducer(unacked=3), metrics)
        publisher.publish("sms.events", _one_per_topic(emitted)["sms.events"])
        with pytest.raises(PublishError, match="3 message"):
            publisher.flush()

    def test_a_full_local_queue_is_drained_and_retried(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        producer = FakeProducer(full_queue=4)
        publisher = _publisher(producer, metrics)
        publisher.publish("sms.events", _one_per_topic(emitted)["sms.events"])
        publisher.flush()
        assert len(producer.on("sms.events")) == 1
        assert producer.polls >= 4
        assert publisher.stats.total_published == 1

    def test_a_queue_that_never_drains_is_reported(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        publisher = _publisher(FakeProducer(full_queue=10_000), metrics)
        publisher.publish("sms.events", _one_per_topic(emitted)["sms.events"])
        with pytest.raises(PublishError, match="queue stayed full"):
            publisher.flush()

    def test_a_dead_letter_that_cannot_be_delivered_also_fails_the_run(
        self, emitted: list[Emitted], metrics: PlatformMetrics
    ) -> None:
        publisher = _publisher(FakeProducer(fail_delivery=True), metrics)
        event = _one_per_topic(emitted)["sms.events"]
        publisher.publish(
            "sms.events",
            corrupt(
                SPECS["sms.events"], event.model_dump(), Poison.MISSING_TENANT, random.Random(1)
            ),
        )
        with pytest.raises(PublishError):
            publisher.flush()


class TestProducerSettings:
    def test_settings_match_adr_002(self) -> None:
        settings = producer_settings("kafka:9092")
        assert settings["acks"] == "all"
        assert settings["enable.idempotence"] is True
        assert settings["max.in.flight.requests.per.connection"] == 5
        assert settings["retries"] == 2_147_483_647
        assert settings["bootstrap.servers"] == "kafka:9092"

    def test_the_address_family_defaults_to_ipv4_for_docker_desktop(self) -> None:
        assert producer_settings("x")["broker.address.family"] == "v4"
        assert producer_settings("x", address_family="any")["broker.address.family"] == "any"

    def test_config_defaults_match_the_compose_core_profile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("KAFKA_BOOTSTRAP_SERVERS", "SCHEMA_REGISTRY_URL", "KAFKA_ADDRESS_FAMILY"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.chdir("/")
        config = PublisherConfig()
        assert config.kafka_bootstrap_servers == "localhost:9092"
        assert config.schema_registry_url == "http://127.0.0.1:8081"

    def test_the_registry_default_is_not_the_name_localhost(self) -> None:
        """`localhost` costs ~2 s per request on Windows (IPv6 tried first, nobody listens)."""
        assert "localhost" not in PublisherConfig.model_fields["schema_registry_url"].default
        template = (DEFAULT_SCHEMA_DIR.parents[2] / ".env.example").read_text(encoding="utf-8")
        assert "SCHEMA_REGISTRY_URL=http://127.0.0.1:8081" in template

    def test_config_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "broker:1234")
        assert PublisherConfig().kafka_bootstrap_servers == "broker:1234"


# -- Command line -------------------------------------------------------------------------------


class TestCli:
    @pytest.fixture
    def wired(
        self, registry_server: tuple[str, dict], monkeypatch: pytest.MonkeyPatch
    ) -> FakeProducer:
        monkeypatch.setenv("SCHEMA_REGISTRY_URL", registry_server[0])
        monkeypatch.chdir("/")
        producer = FakeProducer()
        monkeypatch.setattr(cli, "Producer", lambda settings: producer)
        return producer

    def test_register_registers_and_sends_nothing(
        self, wired: FakeProducer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["register"]) == 0
        assert "Registered 22 subjects" in capsys.readouterr().out
        assert wired.sent == []

    def test_run_publishes_and_reports(
        self, wired: FakeProducer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["run", "--duration-minutes", "5", "--scenario", "none"]) == 0
        out = capsys.readouterr().out
        assert "Published" in out and "dead-lettered" in out
        assert wired.on("cdr.events")
        assert not [m for m in wired.sent if m["topic"].endswith(".dlq")]

    def test_poison_fraction_fills_the_dead_letter_queues(
        self, wired: FakeProducer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            cli.main(
                ["run", "--duration-minutes", "5", "--scenario", "none", "--poison-fraction", "0.2"]
            )
            == 0
        )
        dead = [m for m in wired.sent if m["topic"].endswith(".dlq")]
        assert len(dead) > 0.1 * len(wired.sent)
        assert "DLQ " in capsys.readouterr().out

    def test_a_run_is_reproducible(
        self, registry_server: tuple[str, dict], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCHEMA_REGISTRY_URL", registry_server[0])
        monkeypatch.chdir("/")
        runs = []
        for _ in range(2):
            producer = FakeProducer()
            monkeypatch.setattr(cli, "Producer", lambda settings, p=producer: p)
            cli.main(
                ["run", "--duration-minutes", "3", "--scenario", "all", "--poison-fraction", "0.1"]
            )
            runs.append([(m["topic"], m["key"]) for m in producer.sent])
        assert runs[0] == runs[1]

    @pytest.mark.parametrize(
        "args",
        [
            ["--duration-minutes", "0"],
            ["--poison-fraction", "1.5"],
            ["--start", "2026-09-01T10:00:00"],
        ],
    )
    def test_invalid_arguments_exit_with_code_two(
        self, wired: FakeProducer, args: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["run", *args]) == 2
        assert "ERROR" in capsys.readouterr().err
        assert wired.sent == []

    def test_an_unreachable_registry_exits_with_code_two(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SCHEMA_REGISTRY_URL", "http://127.0.0.1:9")
        monkeypatch.chdir("/")
        assert cli.main(["register"]) == 2
        assert "unreachable" in capsys.readouterr().err

    def test_a_delivery_failure_exits_with_code_one(
        self, registry_server: tuple[str, dict], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCHEMA_REGISTRY_URL", registry_server[0])
        monkeypatch.chdir("/")
        monkeypatch.setattr(cli, "Producer", lambda settings: FakeProducer(fail_delivery=True))
        assert cli.main(["run", "--duration-minutes", "2", "--scenario", "none"]) == 1
