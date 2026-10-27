# GENERATED FILE - DO NOT EDIT.
# Source: data-platform/schemas/avro/network-metrics-v1.avsc
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)
# Editing this file by hand is pointless: CI regenerates it and fails on any difference.

from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from telecom_common.contracts.base import ContractModel, UtcDatetime, not_in_future
from telecom_common.domain.enums import Technology

__all__ = ['NetworkMetrics']


class NetworkMetrics(ContractModel):
    """Métriques de performance radio agrégées par cellule sur une fenêtre glissante de 60 secondes. Produit par l'EMS radio via l'adaptateur d'ingestion."""

    __contract__: ClassVar[str] = 'network-metrics-v1'
    __topic__: ClassVar[str] = 'network.metrics'
    __partition_key__: ClassVar[str] = 'cell_id'

    event_id: str = Field(description='UUID v7 unique. Clé de déduplication (FR-M2-003).')
    event_timestamp: Annotated[UtcDatetime, AfterValidator(not_in_future)] = Field(description='Instant de fin de la fenêtre de mesure, en UTC. Base du partitionnement et du watermarking.')
    ingestion_timestamp: UtcDatetime = Field(description="Instant d'entrée dans la plateforme, en UTC. Sert au calcul de fraîcheur.")
    schema_version: str = Field(default='1.0.0', description="Version sémantique du contrat, portée par l'événement pour faciliter le débogage.")
    producer_id: str = Field(description="Identifiant du système ou de l'adaptateur émetteur (ex. ems-radio-bamako-01).")
    tenant_id: str = Field(description='Identifiant du tenant. OBLIGATOIRE — un message sans tenant est rejeté en DLQ (ADR-016).')
    cell_id: Annotated[str, Field(pattern='^CELL-[0-9]{4}$')] = Field(description='Identifiant de cellule au format CELL-NNNN.')
    site_id: Annotated[str, Field(pattern='^SITE-[0-9]{3,4}$')] = Field(description='Site hébergeant la cellule, au format SITE-NNN.')
    region: str = Field(description='Région administrative. Dimension de partitionnement en silver.')
    technology: Technology = Field(description='Technologie radio. Préfixe G car un symbole Avro ne peut pas commencer par un chiffre.')
    traffic_mb: Annotated[float, Field(ge=0)] = Field(description='Volume de données échangées durant la fenêtre.', json_schema_extra={'unit': 'megabyte'})
    active_users: Annotated[int, Field(ge=0)] = Field(description="Nombre d'utilisateurs simultanément rattachés à la cellule.", json_schema_extra={'unit': 'count'})
    latency_ms: Annotated[float, Field(ge=0, le=5000)] = Field(description='Latence aller-retour moyenne observée.', json_schema_extra={'unit': 'millisecond'})
    packet_loss_pct: Annotated[float, Field(ge=0, le=100)] = Field(description='Taux de perte de paquets.', json_schema_extra={'unit': 'percent'})
    drop_rate_pct: Annotated[float, Field(ge=0, le=100)] = Field(description="Taux d'appels interrompus (Call Drop Rate).", json_schema_extra={'unit': 'percent'})
    call_setup_failure_pct: Annotated[float, Field(ge=0, le=100)] = Field(description="Taux d'échec d'établissement d'appel.", json_schema_extra={'unit': 'percent'})
    throughput_mbps: Annotated[float, Field(ge=0)] = Field(description='Débit utile moyen par utilisateur.', json_schema_extra={'unit': 'megabit_per_second'})
    signal_strength_dbm: Annotated[float, Field(ge=-140, le=-40)] = Field(description='Puissance de signal moyenne reçue (RSRP en 4G/5G, RSCP en 3G).', json_schema_extra={'unit': 'dbm'})
    prb_utilization_pct: Annotated[float, Field(ge=0, le=100)] = Field(description="Taux d'occupation des Physical Resource Blocks. Indicateur principal de saturation radio.", json_schema_extra={'unit': 'percent'})
    availability_pct: Annotated[float, Field(ge=0, le=100)] = Field(default=100.0, description='Disponibilité de la cellule sur la fenêtre.', json_schema_extra={'unit': 'percent'})
