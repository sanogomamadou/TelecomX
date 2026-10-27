"""Synthetic event generators for the eleven Kafka topics."""

from __future__ import annotations

from telecomx_generators.events.config import PRODUCTION_RATES, GeneratorConfig
from telecomx_generators.events.context import GenContext
from telecomx_generators.events.engine import Emitted, generate, generate_topic
from telecomx_generators.events.scenarios import (
    CellDegradation,
    FraudBurst,
    GroundTruth,
    Scenario,
)

__all__ = [
    "PRODUCTION_RATES",
    "CellDegradation",
    "Emitted",
    "FraudBurst",
    "GenContext",
    "GeneratorConfig",
    "GroundTruth",
    "Scenario",
    "generate",
    "generate_topic",
]
