"""Synthetic reference data: regions, sites, cells, equipment and catalogues."""

from __future__ import annotations

from telecomx_generators.reference.build import (
    ANCHORS,
    CELLS_BY_TECHNOLOGY,
    DEFAULT_SEED,
    SITE_COUNT,
    TENANT_ID,
    TOTAL_SUBSCRIBERS,
    ScenarioAnchors,
    build_catalog,
)

__all__ = [
    "ANCHORS",
    "CELLS_BY_TECHNOLOGY",
    "DEFAULT_SEED",
    "SITE_COUNT",
    "TENANT_ID",
    "TOTAL_SUBSCRIBERS",
    "ScenarioAnchors",
    "build_catalog",
]
