# GENERATED FILE - DO NOT EDIT.
# Source: data-platform/schemas/avro/data-session-v1.avsc
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)
# Editing this file by hand is pointless: CI regenerates it and fails on any difference.

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from telecom_common.contracts.base import ContractModel, UtcDatetime, not_in_future
from telecom_common.domain.enums import Technology

__all__ = ['DataSession', 'TerminationCause']


class TerminationCause(StrEnum):
    """Avro enum ``TerminationCause`` of contract ``data-session-v1``."""

    NORMAL = 'NORMAL'
    TIMEOUT = 'TIMEOUT'
    QUOTA_EXHAUSTED = 'QUOTA_EXHAUSTED'
    NETWORK_FAILURE = 'NETWORK_FAILURE'
    HANDOVER_FAILURE = 'HANDOVER_FAILURE'
    USER_DETACH = 'USER_DETACH'


class DataSession(ContractModel):
    """Session de données mobiles (PDP context / PDU session). Base du traffic analytics et de la mesure de qualité vécue par l'abonné."""

    __contract__: ClassVar[str] = 'data-session-v1'
    __topic__: ClassVar[str] = 'data.sessions'
    __partition_key__: ClassVar[str] = 'subscriber_id'
    __sum_rules__: ClassVar[tuple[tuple[str, tuple[str, ...]], ...]] = (('total_volume_mb', ('download_mb', 'upload_mb')),)

    event_id: str = Field(description='UUID v7, unique per event. Deduplication key.')
    event_timestamp: Annotated[UtcDatetime, AfterValidator(not_in_future)] = Field(description='Instant de fin de session, en UTC.')
    ingestion_timestamp: UtcDatetime = Field(description='Instant the event entered the platform, in UTC. Drives freshness.')
    schema_version: str = Field(default='1.0.0', description='Semantic version of the contract, carried by the event for debugging.')
    producer_id: str = Field(description='Identifier of the emitting system or adapter.')
    tenant_id: str = Field(description='Tenant owning the event. A message without it is routed to the DLQ (ADR-016).')
    session_id: str = Field(description='Identifiant technique de la session.')
    subscriber_id: str = Field(description="MSISDN de l'abonné. IDENTIFIANT DIRECT — tokenisé en subscriber_token.", json_schema_extra={'pii': 'direct'})
    imei: str | None = Field(default=None, description="Identifiant d'appareil. IDENTIFIANT INDIRECT — tokenisé en device_token.", json_schema_extra={'pii': 'indirect'})
    ip_address: str | None = Field(default=None, description='Adresse IP attribuée. IDENTIFIANT INDIRECT — tronquée au /24 en silver, jamais conservée entière.', json_schema_extra={'pii': 'indirect'})
    cell_id: str = Field(description="Cellule servante. Associé à l'abonné, constitue une donnée de localisation (P3).")
    technology: Technology = Field(description='Technologie radio utilisée pendant la session.')
    apn: str = Field(description='Access Point Name (ex. internet, mms, iot). Distingue les usages.')
    duration_seconds: Annotated[int, Field(ge=0, le=86400)] = Field(description='Durée de la session.', json_schema_extra={'unit': 'second'})
    download_mb: Annotated[float, Field(ge=0)] = Field(description='Volume descendant échangé.', json_schema_extra={'unit': 'megabyte'})
    upload_mb: Annotated[float, Field(ge=0)] = Field(description='Volume montant échangé.', json_schema_extra={'unit': 'megabyte'})
    total_volume_mb: Annotated[float, Field(ge=0)] = Field(description='Volume total. Règle métier vérifiée en silver : total_volume_mb = download_mb + upload_mb (tolérance 0,01).', json_schema_extra={'unit': 'megabyte'})
    avg_throughput_mbps: Annotated[float, Field(ge=0)] = Field(description='Débit moyen observé durant la session — mesure directe de la qualité vécue.', json_schema_extra={'unit': 'megabit_per_second'})
    termination_cause: TerminationCause = Field(default=TerminationCause.NORMAL, description='NETWORK_FAILURE et HANDOVER_FAILURE sont des indicateurs de dégradation réseau.')
    bundle_code: str | None = Field(default=None, description='Forfait ayant financé la session, si applicable.')
    charged_amount: Annotated[float, Field(ge=0)] = Field(default=0.0, description='Montant facturé pour la session, hors forfait.', json_schema_extra={'unit': 'xof'})
    is_roaming: bool = Field(default=False, description='Session réalisée en itinérance.')
