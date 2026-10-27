"""The eleven event streams, keyed by Kafka topic."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final

from telecom_common.contracts.base import ContractModel
from telecomx_generators.events.context import GenContext
from telecomx_generators.events.streams.commerce import bundles, mobile_money, recharges
from telecomx_generators.events.streams.customer import customer_events
from telecomx_generators.events.streams.network import alarms, equipment_telemetry, network_metrics
from telecomx_generators.events.streams.traffic import cdr, data_sessions, sms, ussd

__all__ = ["STREAMS", "StreamFn"]

StreamFn = Callable[[GenContext, datetime], list[ContractModel]]

#: One generator per topic. Each takes a context and the start of a 60-second bucket and
#: returns the events that *happened* in that bucket.
STREAMS: Final[dict[str, StreamFn]] = {
    "cdr.events": cdr,  # type: ignore[dict-item]
    "data.sessions": data_sessions,  # type: ignore[dict-item]
    "sms.events": sms,  # type: ignore[dict-item]
    "ussd.events": ussd,  # type: ignore[dict-item]
    "recharge.events": recharges,  # type: ignore[dict-item]
    "bundle.events": bundles,  # type: ignore[dict-item]
    "mobilemoney.transactions": mobile_money,  # type: ignore[dict-item]
    "customer.events": customer_events,  # type: ignore[dict-item]
    "network.metrics": network_metrics,  # type: ignore[dict-item]
    "network.alarms": alarms,  # type: ignore[dict-item]
    "device.events": equipment_telemetry,  # type: ignore[dict-item]
}
