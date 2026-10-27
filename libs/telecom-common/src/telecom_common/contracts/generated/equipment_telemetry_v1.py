# GENERATED FILE - DO NOT EDIT.
# Source: data-platform/schemas/avro/equipment-telemetry-v1.avsc
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)
# Editing this file by hand is pointless: CI regenerates it and fails on any difference.

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from telecom_common.contracts.base import ContractModel, UtcDatetime, not_in_future

__all__ = ['EquipmentTelemetry', 'EquipmentType', 'FanStatus', 'OperationalStatus']


class EquipmentType(StrEnum):
    """Avro enum ``EquipmentType`` of contract ``equipment-telemetry-v1``."""

    BBU = 'BBU'
    RRU = 'RRU'
    ROUTER = 'ROUTER'
    SWITCH = 'SWITCH'
    POWER_SUPPLY = 'POWER_SUPPLY'
    BATTERY = 'BATTERY'
    GENSET = 'GENSET'
    HVAC = 'HVAC'
    MICROWAVE_LINK = 'MICROWAVE_LINK'
    FIBER_TERMINAL = 'FIBER_TERMINAL'


class FanStatus(StrEnum):
    """Avro enum ``FanStatus`` of contract ``equipment-telemetry-v1``."""

    OK = 'OK'
    DEGRADED = 'DEGRADED'
    FAILED = 'FAILED'
    NOT_APPLICABLE = 'NOT_APPLICABLE'


class OperationalStatus(StrEnum):
    """Avro enum ``OperationalStatus`` of contract ``equipment-telemetry-v1``."""

    NORMAL = 'NORMAL'
    DEGRADED = 'DEGRADED'
    MAINTENANCE = 'MAINTENANCE'
    FAULT = 'FAULT'
    OFFLINE = 'OFFLINE'


class EquipmentTelemetry(ContractModel):
    """Télémétrie des équipements d'infrastructure (BBU, routeurs, alimentation, climatisation, groupes électrogènes). Source unique du modèle de maintenance prédictive."""

    __contract__: ClassVar[str] = 'equipment-telemetry-v1'
    __topic__: ClassVar[str] = 'device.events'
    __partition_key__: ClassVar[str] = 'equipment_id'

    event_id: str = Field(description='UUID v7, unique per event. Deduplication key.')
    event_timestamp: Annotated[UtcDatetime, AfterValidator(not_in_future)] = Field(description='Instant the event occurred in the real world, in UTC.')
    ingestion_timestamp: UtcDatetime = Field(description='Instant the event entered the platform, in UTC. Drives freshness.')
    schema_version: str = Field(default='1.0.0', description='Semantic version of the contract, carried by the event for debugging.')
    producer_id: str = Field(description='Identifier of the emitting system or adapter.')
    tenant_id: str = Field(description='Tenant owning the event. A message without it is routed to the DLQ (ADR-016).')
    equipment_id: str = Field(description="Identifiant d'équipement (ex. ROUTER-082, BBU-014, GENSET-201).")
    site_id: str = Field(description="Site hébergeant l'équipement.")
    equipment_type: EquipmentType = Field(description="Catégorie d'équipement. Détermine le profil de défaillance attendu.")
    vendor: str | None = Field(default=None, description='Constructeur. Certains modèles ont des profils de défaillance spécifiques.')
    model: str | None = Field(default=None, description='Référence du modèle. Certains modèles présentent des défaillances caractéristiques.')
    temperature_celsius: Annotated[float, Field(ge=-20, le=120)] | None = Field(default=None, description='Température interne. Prédicteur de panne le plus fort, surtout combiné à une défaillance de climatisation.', json_schema_extra={'unit': 'celsius'})
    cpu_utilization_pct: Annotated[float, Field(ge=0, le=100)] | None = Field(default=None, description="Taux d'occupation processeur. Une saturation prolongée précède souvent une dégradation de service.", json_schema_extra={'unit': 'percent'})
    memory_utilization_pct: Annotated[float, Field(ge=0, le=100)] | None = Field(default=None, description="Taux d'occupation mémoire. Une croissance monotone signale une fuite mémoire.", json_schema_extra={'unit': 'percent'})
    uptime_seconds: Annotated[int, Field(ge=0)] = Field(description='Durée depuis le dernier redémarrage. Une remise à zéro inattendue signale un redémarrage non planifié.', json_schema_extra={'unit': 'second'})
    power_consumption_watts: Annotated[float, Field(ge=0)] | None = Field(default=None, description="Consommation électrique instantanée. Une dérive est un indicateur d'usure.", json_schema_extra={'unit': 'watt'})
    input_voltage: Annotated[float, Field(ge=0)] | None = Field(default=None, description="Tension d'alimentation. Les instabilités électriques sont une cause majeure de panne.", json_schema_extra={'unit': 'volt'})
    battery_level_pct: Annotated[float, Field(ge=0, le=100)] | None = Field(default=None, description='Niveau de batterie de secours.', json_schema_extra={'unit': 'percent'})
    battery_cycles: Annotated[int, Field(ge=0)] | None = Field(default=None, description="Nombre de cycles de charge/décharge. Indicateur d'usure.")
    crc_error_count: Annotated[int, Field(ge=0)] | None = Field(default=None, description='Erreurs CRC sur les interfaces transport. Une croissance progressive annonce une dégradation de lien (fibre, faisceau hertzien).')
    packet_error_rate: Annotated[float, Field(ge=0, le=100)] | None = Field(default=None, description="Taux d'erreur paquets sur les interfaces.", json_schema_extra={'unit': 'percent'})
    link_utilization_pct: Annotated[float, Field(ge=0, le=100)] | None = Field(default=None, description="Taux d'occupation du lien de transport. Une saturation du backhaul se manifeste ici avant de dégrader les KPI radio — signal clé de l'analyse de cause racine.", json_schema_extra={'unit': 'percent'})
    fan_status: FanStatus | None = Field(default=None, description='État de la ventilation. Une ventilation dégradée combinée à une température élevée est le scénario de panne le plus fréquent.')
    operational_status: OperationalStatus = Field(default=OperationalStatus.NORMAL, description="État opérationnel déclaré par l'équipement.")
    days_since_maintenance: Annotated[int, Field(ge=0)] | None = Field(default=None, description='Jours écoulés depuis la dernière intervention. Feature directe du modèle de maintenance prédictive.', json_schema_extra={'unit': 'day'})
