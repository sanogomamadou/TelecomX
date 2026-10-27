# GENERATED FILE - DO NOT EDIT.
# Source: data-platform/schemas/avro/recharge-v1.avsc
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)
# Editing this file by hand is pointless: CI regenerates it and fails on any difference.

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from telecom_common.contracts.base import ContractModel, UtcDatetime, not_in_future

__all__ = ['RechargeEvent', 'RechargeChannel', 'RechargeStatus']


class RechargeChannel(StrEnum):
    """Avro enum ``RechargeChannel`` of contract ``recharge-v1``."""

    SCRATCH_CARD = 'SCRATCH_CARD'
    MOBILE_MONEY = 'MOBILE_MONEY'
    AGENT = 'AGENT'
    BANK = 'BANK'
    APP = 'APP'
    USSD = 'USSD'
    ATM = 'ATM'


class RechargeStatus(StrEnum):
    """Avro enum ``RechargeStatus`` of contract ``recharge-v1``."""

    SUCCESS = 'SUCCESS'
    FAILED = 'FAILED'
    PENDING = 'PENDING'
    REVERSED = 'REVERSED'


class RechargeEvent(ContractModel):
    """Rechargement de crédit prépayé. La fréquence et le montant des recharges sont parmi les prédicteurs les plus forts du churn."""

    __contract__: ClassVar[str] = 'recharge-v1'
    __topic__: ClassVar[str] = 'recharge.events'
    __partition_key__: ClassVar[str] = 'subscriber_id'

    event_id: str = Field(description='UUID v7, unique per event. Deduplication key.')
    event_timestamp: Annotated[UtcDatetime, AfterValidator(not_in_future)] = Field(description='Instant the event occurred in the real world, in UTC.')
    ingestion_timestamp: UtcDatetime = Field(description='Instant the event entered the platform, in UTC. Drives freshness.')
    schema_version: str = Field(default='1.0.0', description='Semantic version of the contract, carried by the event for debugging.')
    producer_id: str = Field(description='Identifier of the emitting system or adapter.')
    tenant_id: str = Field(description='Tenant owning the event. A message without it is routed to the DLQ (ADR-016).')
    recharge_id: str = Field(description="Identifiant unique de l'opération de recharge.")
    subscriber_id: str = Field(description='MSISDN rechargé. IDENTIFIANT DIRECT — tokenisé.', json_schema_extra={'pii': 'direct'})
    amount: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)] = Field(description='Montant rechargé.', json_schema_extra={'unit': 'xof'})
    channel: RechargeChannel = Field(description='Canal de rechargement. MOBILE_MONEY est le canal dominant.')
    agent_id: str | None = Field(default=None, description='Agent ou point de vente. Un agent atypique est un signal de fraude.')
    balance_after: Annotated[Decimal, Field(max_digits=18, decimal_places=2)] | None = Field(default=None, description='Solde après rechargement.', json_schema_extra={'unit': 'xof'})
    status: RechargeStatus = Field(default=RechargeStatus.SUCCESS, description="Issue de l'opération.")
    region: str | None = Field(default=None, description='Région du point de recharge.')
    is_first_recharge: bool = Field(default=False, description="Première recharge de l'abonné — marque l'activation effective.")
