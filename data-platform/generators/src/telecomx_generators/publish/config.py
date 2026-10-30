"""Settings of the Kafka publisher."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from telecom_common.config import BaseConfig

__all__ = ["DEFAULT_SCHEMA_DIR", "PublisherConfig", "producer_settings"]

#: Where the Avro contracts live in a source checkout. The publisher reads the ``.avsc``
#: files themselves (not a copy) so that what is registered is exactly what is reviewed.
DEFAULT_SCHEMA_DIR: Final = (
    Path(__file__).resolve().parents[5] / "data-platform" / "schemas" / "avro"
)


class PublisherConfig(BaseConfig):
    """Where Kafka and the Schema Registry are, as seen from the machine running the publisher.

    Read from the environment or ``.env`` (``KAFKA_BOOTSTRAP_SERVERS``,
    ``SCHEMA_REGISTRY_URL``). The defaults match the Compose ``core`` profile, whose
    broker advertises ``localhost:9092`` to the host (ADR-020).
    """

    kafka_bootstrap_servers: str = "localhost:9092"
    #: A numeric address on purpose: on Windows, ``localhost`` resolves to ``::1`` first, where
    #: Docker does not listen, and every HTTP request then waits ~2 s before falling back to
    #: IPv4 (measured: 2.07 s per request through ``localhost``, 0.02 s through 127.0.0.1).
    schema_registry_url: str = "http://127.0.0.1:8081"
    #: Docker Desktop publishes the broker on the IPv4 loopback only. Left on "any", the
    #: client tries ``::1`` first and logs a connection failure every two seconds.
    kafka_address_family: str = "v4"


def producer_settings(
    bootstrap_servers: str,
    *,
    client_id: str = "telecomx-generators",
    address_family: str = "v4",
) -> dict[str, Any]:
    """Producer settings required by ADR-002 and FR-M1-007.

    * ``acks=all`` and idempotence: a message acknowledged is on the broker, and a retry
      after a network error never writes it twice;
    * ``max.in.flight=5``: the highest value that keeps ordering under idempotence;
    * ``retries`` at its maximum: delivery is retried until ``delivery.timeout.ms``
      expires, so a transient failure does not lose an event (at-least-once);
    * ``linger.ms`` and compression trade a few milliseconds of latency for throughput.
    """
    return {
        "bootstrap.servers": bootstrap_servers,
        "client.id": client_id,
        "broker.address.family": address_family,
        "acks": "all",
        "enable.idempotence": True,
        "max.in.flight.requests.per.connection": 5,
        "retries": 2_147_483_647,
        "delivery.timeout.ms": 120_000,
        "linger.ms": 20,
        "compression.type": "lz4",
    }
