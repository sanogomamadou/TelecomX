# GENERATED FILE - DO NOT EDIT.
# Source: data-platform/schemas/avro/network-alarms-v1.avsc
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)
# Editing this file by hand is pointless: CI regenerates it and fails on any difference.

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from telecom_common.contracts.base import ContractModel, UtcDatetime, not_in_future

__all__ = ['NetworkAlarm', 'AlarmDomain', 'AlarmSeverity', 'AlarmSource', 'AlarmStatus']


class AlarmSeverity(StrEnum):
    """Avro enum ``AlarmSeverity`` of contract ``network-alarms-v1``."""

    CRITICAL = 'CRITICAL'
    MAJOR = 'MAJOR'
    MINOR = 'MINOR'
    WARNING = 'WARNING'
    INFO = 'INFO'


class AlarmDomain(StrEnum):
    """Avro enum ``AlarmDomain`` of contract ``network-alarms-v1``."""

    RADIO = 'RADIO'
    TRANSPORT = 'TRANSPORT'
    CORE = 'CORE'
    POWER = 'POWER'
    ENVIRONMENT = 'ENVIRONMENT'
    PLATFORM = 'PLATFORM'


class AlarmStatus(StrEnum):
    """Avro enum ``AlarmStatus`` of contract ``network-alarms-v1``."""

    ACTIVE = 'ACTIVE'
    ACKNOWLEDGED = 'ACKNOWLEDGED'
    CLEARED = 'CLEARED'


class AlarmSource(StrEnum):
    """Avro enum ``AlarmSource`` of contract ``network-alarms-v1``."""

    EMS = 'EMS'
    ANOMALY_DETECTION = 'ANOMALY_DETECTION'
    MANUAL = 'MANUAL'
    THRESHOLD = 'THRESHOLD'


class NetworkAlarm(ContractModel):
    """Alarme émise par un équipement réseau ou par la plateforme elle-même (anomalie détectée par ML). Topic à faible débit mais forte valeur : rétention 30 jours."""

    __contract__: ClassVar[str] = 'network-alarms-v1'
    __topic__: ClassVar[str] = 'network.alarms'
    __partition_key__: ClassVar[str] = 'site_id'

    event_id: str = Field(description='UUID v7, unique per event. Deduplication key.')
    event_timestamp: Annotated[UtcDatetime, AfterValidator(not_in_future)] = Field(description="Instant de déclenchement de l'alarme, en UTC.")
    ingestion_timestamp: UtcDatetime = Field(description='Instant the event entered the platform, in UTC. Drives freshness.')
    schema_version: str = Field(default='1.0.0', description='Semantic version of the contract, carried by the event for debugging.')
    producer_id: str = Field(description='Identifier of the emitting system or adapter.')
    tenant_id: str = Field(description='Tenant owning the event. A message without it is routed to the DLQ (ADR-016).')
    alarm_id: str = Field(description="Identifiant unique de l'occurrence d'alarme, au format ALM-YYYYMMDD-NNNNNN.")
    alarm_code: str = Field(description="Code normalisé du type d'alarme (ex. TRX_FAILURE, BACKHAUL_CONGESTION, VSWR_HIGH).")
    severity: AlarmSeverity = Field(description='Sévérité selon la nomenclature ITU-T X.733.')
    domain: AlarmDomain = Field(description="Domaine technique concerné. Détermine l'équipe de traitement et la procédure applicable.")
    site_id: str = Field(description="Site concerné par l'alarme.")
    cell_id: str | None = Field(default=None, description="Cellule concernée si l'alarme est de portée cellulaire. Nul pour une alarme de site ou d'équipement.")
    equipment_id: str | None = Field(default=None, description='Équipement concerné si identifiable (ex. ROUTER-082, BBU-014).')
    region: str = Field(description="Région du site. Permet l'agrégation géographique des incidents.")
    description: str = Field(description="Libellé textuel de l'alarme. ATTENTION : champ de texte libre provenant d'un système externe — traité comme donnée NON FIABLE et jamais interpolé dans la zone d'instructions d'un prompt (voir threat model §5.2).")
    probable_cause: str | None = Field(default=None, description="Cause probable déclarée par l'équipement, selon la nomenclature constructeur.")
    status: AlarmStatus = Field(default=AlarmStatus.ACTIVE, description="État courant de l'alarme dans son cycle de vie.")
    cleared_timestamp: UtcDatetime | None = Field(default=None, description="Instant de résolution, en UTC. Permet de calculer la durée de l'alarme.")
    source: AlarmSource = Field(default=AlarmSource.EMS, description="Origine de l'alarme. ANOMALY_DETECTION identifie les alarmes générées par le modèle ML de la plateforme — c'est ce qui déclenche l'Operations Agent.")
    anomaly_score: Annotated[float, Field(ge=0, le=1)] | None = Field(default=None, description="Score d'anomalie [0,1] lorsque source = ANOMALY_DETECTION.")
    correlation_id: str | None = Field(default=None, description="Regroupe les alarmes issues d'une même cause racine (une panne de site en génère des dizaines).")
