# GENERATED FILE - DO NOT EDIT.
# Source: data-platform/schemas/avro/bundle-v1.avsc
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)
# Editing this file by hand is pointless: CI regenerates it and fails on any difference.

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from telecom_common.contracts.base import ContractModel, UtcDatetime, not_in_future

__all__ = ['BundleEvent', 'BundleChannel', 'BundleEventType', 'BundleType']


class BundleEventType(StrEnum):
    """Avro enum ``BundleEventType`` of contract ``bundle-v1``."""

    PURCHASED = 'PURCHASED'
    ACTIVATED = 'ACTIVATED'
    CONSUMED = 'CONSUMED'
    EXPIRED = 'EXPIRED'
    RENEWED = 'RENEWED'
    CANCELLED = 'CANCELLED'


class BundleType(StrEnum):
    """Avro enum ``BundleType`` of contract ``bundle-v1``."""

    DATA = 'DATA'
    VOICE = 'VOICE'
    SMS = 'SMS'
    MIXED = 'MIXED'
    SOCIAL = 'SOCIAL'


class BundleChannel(StrEnum):
    """Avro enum ``BundleChannel`` of contract ``bundle-v1``."""

    USSD = 'USSD'
    APP = 'APP'
    AGENT = 'AGENT'
    WEB = 'WEB'
    AUTO_RENEWAL = 'AUTO_RENEWAL'
    SMS = 'SMS'


class BundleEvent(ContractModel):
    """Cycle de vie d'un forfait (achat, activation, consommation, expiration). Le taux d'utilisation d'un forfait est un signal de churn : un abonné qui n'épuise plus ses bundles se désengage."""

    __contract__: ClassVar[str] = 'bundle-v1'
    __topic__: ClassVar[str] = 'bundle.events'
    __partition_key__: ClassVar[str] = 'subscriber_id'

    event_id: str = Field(description='UUID v7, unique per event. Deduplication key.')
    event_timestamp: Annotated[UtcDatetime, AfterValidator(not_in_future)] = Field(description='Instant the event occurred in the real world, in UTC.')
    ingestion_timestamp: UtcDatetime = Field(description='Instant the event entered the platform, in UTC. Drives freshness.')
    schema_version: str = Field(default='1.0.0', description='Semantic version of the contract, carried by the event for debugging.')
    producer_id: str = Field(description='Identifier of the emitting system or adapter.')
    tenant_id: str = Field(description='Tenant owning the event. A message without it is routed to the DLQ (ADR-016).')
    subscriber_id: str = Field(description="MSISDN de l'abonné. IDENTIFIANT DIRECT — tokenisé en subscriber_token.", json_schema_extra={'pii': 'direct'})
    bundle_code: str = Field(description='Code du forfait (ex. DATA-10GB, VOICE-100MIN).')
    event_type: BundleEventType = Field(description='Étape du cycle de vie du forfait. EXPIRED avec un consumed_mb faible révèle un forfait gaspillé.')
    bundle_type: BundleType = Field(description='Nature du forfait. SOCIAL désigne les offres limitées à certaines applications.')
    price: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)] = Field(description="Prix payé par l'abonné.", json_schema_extra={'unit': 'xof'})
    allowance_mb: Annotated[float, Field(ge=0)] | None = Field(default=None, description='Volume data inclus.', json_schema_extra={'unit': 'megabyte'})
    allowance_minutes: Annotated[int, Field(ge=0)] | None = Field(default=None, description='Minutes de voix incluses.', json_schema_extra={'unit': 'minute'})
    consumed_mb: Annotated[float, Field(ge=0)] | None = Field(default=None, description="Volume consommé à l'instant de l'événement. Sur EXPIRED, révèle le gaspillage — signal d'insatisfaction tarifaire.", json_schema_extra={'unit': 'megabyte'})
    validity_days: Annotated[int, Field(ge=0)] = Field(default=30, description="Durée de validité du forfait à compter de l'activation.", json_schema_extra={'unit': 'day'})
    expiry_timestamp: UtcDatetime | None = Field(default=None, description="Instant d'expiration du forfait, en UTC.")
    purchase_channel: BundleChannel | None = Field(default=None, description="Canal d'achat. AUTO_RENEWAL identifie les reconductions automatiques.")
    is_auto_renew: bool = Field(default=False, description='Forfait souscrit avec reconduction automatique. Réduit fortement le risque de churn.')
