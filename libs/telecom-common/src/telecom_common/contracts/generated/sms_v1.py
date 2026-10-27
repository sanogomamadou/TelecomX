# GENERATED FILE - DO NOT EDIT.
# Source: data-platform/schemas/avro/sms-v1.avsc
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)
# Editing this file by hand is pointless: CI regenerates it and fails on any difference.

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from telecom_common.contracts.base import ContractModel, UtcDatetime, not_in_future

__all__ = ['SmsEvent', 'SmsDirection', 'SmsStatus', 'SmsType']


class SmsDirection(StrEnum):
    """Avro enum ``SmsDirection`` of contract ``sms-v1``."""

    MO = 'MO'
    MT = 'MT'


class SmsType(StrEnum):
    """Avro enum ``SmsType`` of contract ``sms-v1``."""

    P2P = 'P2P'
    A2P = 'A2P'
    PROMOTIONAL = 'PROMOTIONAL'
    OTP = 'OTP'
    SYSTEM = 'SYSTEM'


class SmsStatus(StrEnum):
    """Avro enum ``SmsStatus`` of contract ``sms-v1``."""

    DELIVERED = 'DELIVERED'
    FAILED = 'FAILED'
    PENDING = 'PENDING'
    EXPIRED = 'EXPIRED'
    REJECTED = 'REJECTED'


class SmsEvent(ContractModel):
    """Métadonnées d'un SMS. Le CONTENU du message n'est JAMAIS collecté (secret des correspondances) — seules les métadonnées le sont. Topic soumis à des pics extrêmes (×28 au Nouvel An)."""

    __contract__: ClassVar[str] = 'sms-v1'
    __topic__: ClassVar[str] = 'sms.events'
    __partition_key__: ClassVar[str] = 'sender_id'

    event_id: str = Field(description='UUID v7, unique per event. Deduplication key.')
    event_timestamp: Annotated[UtcDatetime, AfterValidator(not_in_future)] = Field(description='Instant the event occurred in the real world, in UTC.')
    ingestion_timestamp: UtcDatetime = Field(description='Instant the event entered the platform, in UTC. Drives freshness.')
    schema_version: str = Field(default='1.0.0', description='Semantic version of the contract, carried by the event for debugging.')
    producer_id: str = Field(description='Identifier of the emitting system or adapter.')
    tenant_id: str = Field(description='Tenant owning the event. A message without it is routed to the DLQ (ADR-016).')
    message_id: str = Field(description="Identifiant technique du message. Le CONTENU n'est jamais collecté.")
    sender_id: str = Field(description="MSISDN de l'émetteur, ou nom court pour un SMS applicatif. IDENTIFIANT DIRECT — tokenisé.", json_schema_extra={'pii': 'direct'})
    receiver_id: str = Field(description='MSISDN du destinataire. IDENTIFIANT DIRECT — tokenisé.', json_schema_extra={'pii': 'direct'})
    cell_id: str | None = Field(default=None, description="Cellule d'émission.")
    direction: SmsDirection = Field(description="MO = envoyé par l'abonné, MT = reçu par l'abonné.")
    sms_type: SmsType = Field(default=SmsType.P2P, description="A2P (application-to-person) et OTP sont exclus des analyses d'usage personnel.")
    status: SmsStatus = Field(description='Statut de remise du message.')
    segment_count: Annotated[int, Field(ge=1, le=10)] = Field(default=1, description='Nombre de segments (un SMS long est découpé). Sert à la facturation.')
    charged_amount: Annotated[float, Field(ge=0)] = Field(default=0.0, description="Montant facturé pour l'envoi.", json_schema_extra={'unit': 'xof'})
    is_international: bool = Field(default=False, description="Message à destination de l'international.")
