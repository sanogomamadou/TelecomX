"""Publishing synthetic events to Kafka: schemas, validation, dead-letter queues."""

from __future__ import annotations

from telecomx_generators.publish.config import PublisherConfig, producer_settings
from telecomx_generators.publish.dlq import Rejection, RejectionCode
from telecomx_generators.publish.publisher import EventPublisher, PublishError, PublishStats
from telecomx_generators.publish.registry import RegistryError, SchemaRegistryClient

__all__ = [
    "EventPublisher",
    "PublishError",
    "PublishStats",
    "PublisherConfig",
    "RegistryError",
    "Rejection",
    "RejectionCode",
    "SchemaRegistryClient",
    "producer_settings",
]
