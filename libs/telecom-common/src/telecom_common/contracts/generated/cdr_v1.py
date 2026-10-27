# GENERATED FILE - DO NOT EDIT.
# Source: data-platform/schemas/avro/cdr-v1.avsc
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)
# Editing this file by hand is pointless: CI regenerates it and fails on any difference.

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from telecom_common.contracts.base import ContractModel, UtcDatetime, not_in_future

__all__ = ['CallDetailRecord', 'CallStatus', 'CallType']


class CallType(StrEnum):
    """Avro enum ``CallType`` of contract ``cdr-v1``."""

    MO = 'MO'
    MT = 'MT'
    FORWARDED = 'FORWARDED'


class CallStatus(StrEnum):
    """Avro enum ``CallStatus`` of contract ``cdr-v1``."""

    COMPLETED = 'COMPLETED'
    DROPPED = 'DROPPED'
    FAILED = 'FAILED'
    BUSY = 'BUSY'
    NO_ANSWER = 'NO_ANSWER'
    REJECTED = 'REJECTED'


class CallDetailRecord(ContractModel):
    """Enregistrement de métadonnées d'appel voix. ATTENTION : contient des identifiants directs en bronze — voir ADR-013 et la politique PII. Le contenu des communications n'est JAMAIS collecté."""

    __contract__: ClassVar[str] = 'cdr-v1'
    __topic__: ClassVar[str] = 'cdr.events'
    __partition_key__: ClassVar[str] = 'caller_id'

    event_id: str = Field(description='UUID v7 unique. Clé de déduplication.')
    event_timestamp: Annotated[UtcDatetime, AfterValidator(not_in_future)] = Field(description="Instant de début d'appel, en UTC.")
    ingestion_timestamp: UtcDatetime = Field(description="Instant d'entrée dans la plateforme, en UTC.")
    schema_version: str = Field(default='1.0.0', description='Semantic version of the contract, carried by the event for debugging.')
    producer_id: str = Field(description='Système de médiation émetteur.')
    tenant_id: str = Field(description='Tenant owning the event. A message without it is routed to the DLQ (ADR-016).')
    call_id: str = Field(description="Identifiant technique de l'appel côté cœur de réseau, au format CALL-NNNNNNN.")
    caller_id: str = Field(description="MSISDN de l'appelant. IDENTIFIANT DIRECT — tokenisé en subscriber_token dès silver.", json_schema_extra={'pii': 'direct'})
    callee_id: str = Field(description="MSISDN de l'appelé. IDENTIFIANT DIRECT — tokenisé en callee_token dès silver.", json_schema_extra={'pii': 'direct'})
    imsi: str | None = Field(default=None, description="Identifiant SIM de l'appelant. IDENTIFIANT DIRECT — tokenisé.", json_schema_extra={'pii': 'direct'})
    imei: str | None = Field(default=None, description="Identifiant d'appareil. IDENTIFIANT INDIRECT — tokenisé.", json_schema_extra={'pii': 'indirect'})
    cell_id: str = Field(description="Cellule servante au début de l'appel. Associé à un abonné, devient une donnée de localisation (P3).")
    call_type: CallType = Field(description='MO = Mobile Originated (sortant), MT = Mobile Terminated (entrant).')
    duration_seconds: Annotated[int, Field(ge=0, le=86400)] = Field(description="Durée de l'appel. Zéro si l'appel a échoué ou n'a pas abouti.", json_schema_extra={'unit': 'second'})
    call_status: CallStatus = Field(description="Issue de l'appel. DROPPED alimente le calcul du drop rate.")
    failure_cause: str | None = Field(default=None, description="Code de cause normalisé lorsque le statut n'est pas COMPLETED.")
    is_roaming: bool = Field(default=False, description='Appel passé en itinérance.')
    is_international: bool = Field(default=False, description='Destination internationale. Signal utilisé par la détection de fraude.')
    charged_amount: Annotated[float, Field(ge=0)] = Field(default=0.0, description='Montant facturé.', json_schema_extra={'unit': 'xof'})
    rate_plan: str | None = Field(default=None, description='Code du plan tarifaire appliqué.')
