# GENERATED FILE - DO NOT EDIT.
# Source: data-platform/schemas/avro/ussd-v1.avsc
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)
# Editing this file by hand is pointless: CI regenerates it and fails on any difference.

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from telecom_common.contracts.base import ContractModel, UtcDatetime, not_in_future

__all__ = ['UssdEvent', 'UssdCategory', 'UssdStatus']


class UssdCategory(StrEnum):
    """Avro enum ``UssdCategory`` of contract ``ussd-v1``."""

    BALANCE = 'BALANCE'
    BUNDLE_PURCHASE = 'BUNDLE_PURCHASE'
    MOBILE_MONEY = 'MOBILE_MONEY'
    CUSTOMER_SERVICE = 'CUSTOMER_SERVICE'
    PROMOTION = 'PROMOTION'
    TRANSFER = 'TRANSFER'
    OTHER = 'OTHER'


class UssdStatus(StrEnum):
    """Avro enum ``UssdStatus`` of contract ``ussd-v1``."""

    COMPLETED = 'COMPLETED'
    ABANDONED = 'ABANDONED'
    TIMEOUT = 'TIMEOUT'
    FAILED = 'FAILED'
    NETWORK_ERROR = 'NETWORK_ERROR'


class UssdEvent(ContractModel):
    """Session USSD (menus interactifs *144#, etc.). Canal principal d'accès aux services en zone à faible équipement smartphone : les abandons de session sont un indicateur d'insatisfaction."""

    __contract__: ClassVar[str] = 'ussd-v1'
    __topic__: ClassVar[str] = 'ussd.events'
    __partition_key__: ClassVar[str] = 'subscriber_id'

    event_id: str = Field(description='UUID v7, unique per event. Deduplication key.')
    event_timestamp: Annotated[UtcDatetime, AfterValidator(not_in_future)] = Field(description='Instant the event occurred in the real world, in UTC.')
    ingestion_timestamp: UtcDatetime = Field(description='Instant the event entered the platform, in UTC. Drives freshness.')
    schema_version: str = Field(default='1.0.0', description='Semantic version of the contract, carried by the event for debugging.')
    producer_id: str = Field(description='Identifier of the emitting system or adapter.')
    tenant_id: str = Field(description='Tenant owning the event. A message without it is routed to the DLQ (ADR-016).')
    session_id: str = Field(description='Identifiant technique de la session USSD.')
    subscriber_id: str = Field(description='MSISDN. IDENTIFIANT DIRECT — tokenisé.', json_schema_extra={'pii': 'direct'})
    service_code: str = Field(description='Code USSD composé (ex. *144#). Identifie le service consulté.')
    menu_path: str | None = Field(default=None, description="Chemin de navigation dans l'arborescence (ex. 144>2>1). Permet d'analyser les parcours et les points d'abandon.")
    service_category: UssdCategory = Field(default=UssdCategory.OTHER, description='Catégorie fonctionnelle du service consulté.')
    cell_id: str | None = Field(default=None, description='Cellule servante durant la session.')
    duration_seconds: Annotated[int, Field(ge=0, le=600)] = Field(description='Durée totale de la session.', json_schema_extra={'unit': 'second'})
    step_count: Annotated[int, Field(ge=0)] = Field(default=1, description="Nombre d'interactions dans la session.")
    status: UssdStatus = Field(description="ABANDONED et TIMEOUT alimentent l'analyse d'expérience client et le modèle de churn.")
