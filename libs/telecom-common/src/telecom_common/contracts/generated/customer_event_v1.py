# GENERATED FILE - DO NOT EDIT.
# Source: data-platform/schemas/avro/customer-event-v1.avsc
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)
# Editing this file by hand is pointless: CI regenerates it and fails on any difference.

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from telecom_common.contracts.base import ContractModel, UtcDatetime, not_in_future

__all__ = ['CustomerEvent', 'ComplaintSeverity', 'CustomerChannel', 'CustomerEventType', 'ResolutionStatus']


class CustomerEventType(StrEnum):
    """Avro enum ``CustomerEventType`` of contract ``customer-event-v1``."""

    ACTIVATION = 'ACTIVATION'
    COMPLAINT = 'COMPLAINT'
    SUPPORT_CONTACT = 'SUPPORT_CONTACT'
    PLAN_CHANGE = 'PLAN_CHANGE'
    SIM_SWAP = 'SIM_SWAP'
    DEVICE_CHANGE = 'DEVICE_CHANGE'
    SUSPENSION = 'SUSPENSION'
    REACTIVATION = 'REACTIVATION'
    CHURN = 'CHURN'
    PORT_OUT_REQUEST = 'PORT_OUT_REQUEST'
    SATISFACTION_SURVEY = 'SATISFACTION_SURVEY'


class CustomerChannel(StrEnum):
    """Avro enum ``CustomerChannel`` of contract ``customer-event-v1``."""

    CALL_CENTER = 'CALL_CENTER'
    SHOP = 'SHOP'
    APP = 'APP'
    USSD = 'USSD'
    SMS = 'SMS'
    SOCIAL = 'SOCIAL'
    WEB = 'WEB'


class ComplaintSeverity(StrEnum):
    """Avro enum ``ComplaintSeverity`` of contract ``customer-event-v1``."""

    LOW = 'LOW'
    MEDIUM = 'MEDIUM'
    HIGH = 'HIGH'
    CRITICAL = 'CRITICAL'


class ResolutionStatus(StrEnum):
    """Avro enum ``ResolutionStatus`` of contract ``customer-event-v1``."""

    OPEN = 'OPEN'
    IN_PROGRESS = 'IN_PROGRESS'
    RESOLVED = 'RESOLVED'
    ESCALATED = 'ESCALATED'
    CLOSED_UNRESOLVED = 'CLOSED_UNRESOLVED'


class CustomerEvent(ContractModel):
    """Événement de relation client issu du CRM : réclamations, changements d'offre, interactions support, résiliation. Source de la variable cible du modèle de churn."""

    __contract__: ClassVar[str] = 'customer-event-v1'
    __topic__: ClassVar[str] = 'customer.events'
    __partition_key__: ClassVar[str] = 'subscriber_id'

    event_id: str = Field(description='UUID v7, unique per event. Deduplication key.')
    event_timestamp: Annotated[UtcDatetime, AfterValidator(not_in_future)] = Field(description='Instant the event occurred in the real world, in UTC.')
    ingestion_timestamp: UtcDatetime = Field(description='Instant the event entered the platform, in UTC. Drives freshness.')
    schema_version: str = Field(default='1.0.0', description='Semantic version of the contract, carried by the event for debugging.')
    producer_id: str = Field(description='Identifier of the emitting system or adapter.')
    tenant_id: str = Field(description='Tenant owning the event. A message without it is routed to the DLQ (ADR-016).')
    subscriber_id: str = Field(description="MSISDN de l'abonné concerné. IDENTIFIANT DIRECT — tokenisé en subscriber_token.", json_schema_extra={'pii': 'direct'})
    event_type: CustomerEventType = Field(description='CHURN et PORT_OUT_REQUEST constituent la variable cible du modèle de churn. SIM_SWAP est un signal de fraude majeur (prise de contrôle de compte).')
    channel: CustomerChannel | None = Field(default=None, description="Canal par lequel l'interaction a eu lieu.")
    category: str | None = Field(default=None, description="Catégorie normalisée (NETWORK_QUALITY, BILLING, BUNDLE, MOBILE_MONEY, DEVICE, OTHER). NETWORK_QUALITY permet de corréler les réclamations aux dégradations réseau — clé de l'analyse de cause racine du churn.")
    description: str | None = Field(default=None, description="Texte libre saisi par l'agent. DONNÉE NON FIABLE : traitée comme contenu à analyser, jamais comme instruction (threat model §5.2). Peut contenir des PII saisies manuellement — scannée et masquée en silver.")
    severity: ComplaintSeverity | None = Field(default=None, description='Gravité déclarée de la réclamation.')
    resolution_status: ResolutionStatus | None = Field(default=None, description='État de traitement. CLOSED_UNRESOLVED est un signal de churn particulièrement fort.')
    resolution_time_minutes: Annotated[int, Field(ge=0)] | None = Field(default=None, description='Délai de résolution. Un délai long dégrade la satisfaction.', json_schema_extra={'unit': 'minute'})
    satisfaction_score: Annotated[int, Field(ge=0, le=10)] | None = Field(default=None, description='Score NPS ou CSAT recueilli.')
    related_cell_id: str | None = Field(default=None, description='Cellule mise en cause dans une réclamation de qualité réseau. Relie directement le ressenti client à la performance technique.')
    region: str | None = Field(default=None, description="Région de rattachement de l'abonné.")
    previous_plan: str | None = Field(default=None, description="Offre précédente lors d'un changement de plan.")
    new_plan: str | None = Field(default=None, description="Nouvelle offre lors d'un changement de plan. Un passage vers une offre inférieure précède souvent une résiliation.")
