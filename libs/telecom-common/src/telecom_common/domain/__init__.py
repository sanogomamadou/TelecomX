"""Domain types: validated identifiers and cross-cutting enumerations."""

from __future__ import annotations

from telecom_common.domain.enums import (
    DataZone,
    PiiClass,
    Region,
    RiskClass,
    Technology,
    TenantId,
)
from telecom_common.domain.identifiers import (
    AccountToken,
    AlarmId,
    CallId,
    CellId,
    DeviceToken,
    EquipmentId,
    IncidentId,
    SiteId,
    SubscriberToken,
    TransactionId,
    format_cell_id,
    format_site_id,
    is_valid_identifier,
)

__all__ = [
    "AccountToken",
    "AlarmId",
    "CallId",
    "CellId",
    "DataZone",
    "DeviceToken",
    "EquipmentId",
    "IncidentId",
    "PiiClass",
    "Region",
    "RiskClass",
    "SiteId",
    "SubscriberToken",
    "Technology",
    "TenantId",
    "TransactionId",
    "format_cell_id",
    "format_site_id",
    "is_valid_identifier",
]
