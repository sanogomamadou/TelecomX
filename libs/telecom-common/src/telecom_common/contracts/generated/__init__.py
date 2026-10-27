# GENERATED FILE - DO NOT EDIT.
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)

from __future__ import annotations

from typing import Final

from telecom_common.contracts.base import ContractModel
from telecom_common.contracts.generated.bundle_v1 import BundleEvent
from telecom_common.contracts.generated.cdr_v1 import CallDetailRecord
from telecom_common.contracts.generated.customer_event_v1 import CustomerEvent
from telecom_common.contracts.generated.data_session_v1 import DataSession
from telecom_common.contracts.generated.equipment_telemetry_v1 import EquipmentTelemetry
from telecom_common.contracts.generated.mobilemoney_transaction_v1 import MobileMoneyTransaction
from telecom_common.contracts.generated.network_alarms_v1 import NetworkAlarm
from telecom_common.contracts.generated.network_metrics_v1 import NetworkMetrics
from telecom_common.contracts.generated.recharge_v1 import RechargeEvent
from telecom_common.contracts.generated.sms_v1 import SmsEvent
from telecom_common.contracts.generated.ussd_v1 import UssdEvent

#: Every event model, keyed by contract id (the .avsc file stem).
MODELS: Final[dict[str, type[ContractModel]]] = {
    'bundle-v1': BundleEvent,
    'cdr-v1': CallDetailRecord,
    'customer-event-v1': CustomerEvent,
    'data-session-v1': DataSession,
    'equipment-telemetry-v1': EquipmentTelemetry,
    'mobilemoney-transaction-v1': MobileMoneyTransaction,
    'network-alarms-v1': NetworkAlarm,
    'network-metrics-v1': NetworkMetrics,
    'recharge-v1': RechargeEvent,
    'sms-v1': SmsEvent,
    'ussd-v1': UssdEvent,
}

#: The same models, keyed by Kafka topic. A topic maps to exactly one contract.
BY_TOPIC: Final[dict[str, type[ContractModel]]] = {
    'bundle.events': BundleEvent,
    'cdr.events': CallDetailRecord,
    'customer.events': CustomerEvent,
    'data.sessions': DataSession,
    'device.events': EquipmentTelemetry,
    'mobilemoney.transactions': MobileMoneyTransaction,
    'network.alarms': NetworkAlarm,
    'network.metrics': NetworkMetrics,
    'recharge.events': RechargeEvent,
    'sms.events': SmsEvent,
    'ussd.events': UssdEvent,
}

__all__ = ['BY_TOPIC', 'MODELS', 'BundleEvent', 'CallDetailRecord', 'CustomerEvent', 'DataSession', 'EquipmentTelemetry', 'MobileMoneyTransaction', 'NetworkAlarm', 'NetworkMetrics', 'RechargeEvent', 'SmsEvent', 'UssdEvent']
