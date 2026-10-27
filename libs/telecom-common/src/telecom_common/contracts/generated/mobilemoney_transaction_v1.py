# GENERATED FILE - DO NOT EDIT.
# Source: data-platform/schemas/avro/mobilemoney-transaction-v1.avsc
# Regenerate with: make codegen   (python data-platform/schemas/generate.py)
# Editing this file by hand is pointless: CI regenerates it and fails on any difference.

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, Field

from telecom_common.contracts.base import ContractModel, UtcDatetime, not_in_future

__all__ = ['MobileMoneyTransaction', 'TransactionChannel', 'TransactionStatus', 'TransactionType']


class TransactionType(StrEnum):
    """Avro enum ``TransactionType`` of contract ``mobilemoney-transaction-v1``."""

    P2P = 'P2P'
    CASH_IN = 'CASH_IN'
    CASH_OUT = 'CASH_OUT'
    MERCHANT_PAYMENT = 'MERCHANT_PAYMENT'
    BILL_PAYMENT = 'BILL_PAYMENT'
    AIRTIME_PURCHASE = 'AIRTIME_PURCHASE'
    BULK_DISBURSEMENT = 'BULK_DISBURSEMENT'
    REVERSAL = 'REVERSAL'


class TransactionStatus(StrEnum):
    """Avro enum ``TransactionStatus`` of contract ``mobilemoney-transaction-v1``."""

    COMPLETED = 'COMPLETED'
    PENDING = 'PENDING'
    FAILED = 'FAILED'
    REVERSED = 'REVERSED'
    BLOCKED = 'BLOCKED'


class TransactionChannel(StrEnum):
    """Avro enum ``TransactionChannel`` of contract ``mobilemoney-transaction-v1``."""

    USSD = 'USSD'
    APP = 'APP'
    AGENT = 'AGENT'
    API = 'API'
    WEB = 'WEB'


class MobileMoneyTransaction(ContractModel):
    """Transaction Mobile Money. Donnée financière soumise à des exigences d'audit renforcées : rétention 90 jours sur Kafka, compatibilité FULL, traçabilité intégrale."""

    __contract__: ClassVar[str] = 'mobilemoney-transaction-v1'
    __topic__: ClassVar[str] = 'mobilemoney.transactions'
    __partition_key__: ClassVar[str] = 'sender_account'

    event_id: str = Field(description='UUID v7, unique per event. Deduplication key.')
    event_timestamp: Annotated[UtcDatetime, AfterValidator(not_in_future)] = Field(description='Instant the event occurred in the real world, in UTC.')
    ingestion_timestamp: UtcDatetime = Field(description='Instant the event entered the platform, in UTC. Drives freshness.')
    schema_version: str = Field(default='1.0.0', description='Semantic version of the contract, carried by the event for debugging.')
    producer_id: str = Field(description='Identifier of the emitting system or adapter.')
    tenant_id: str = Field(description='Tenant owning the event. A message without it is routed to the DLQ (ADR-016).')
    transaction_id: str = Field(description='Identifiant unique de transaction, au format TRX-YYYYMMDD-NNNNNNNN.')
    transaction_type: TransactionType = Field(description="Nature de l'opération. CASH_OUT et P2P concentrent l'essentiel des schémas de fraude.")
    sender_account: str = Field(description='Compte émetteur. IDENTIFIANT DIRECT — tokenisé en sender_token dès silver.', json_schema_extra={'pii': 'direct'})
    receiver_account: str = Field(description='Compte destinataire. IDENTIFIANT DIRECT — tokenisé en receiver_token.', json_schema_extra={'pii': 'direct'})
    merchant_id: str | None = Field(default=None, description='Identifiant du marchand pour les paiements. Non tokenisé : entité commerciale, pas personne physique.')
    amount: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)] = Field(description='Montant de la transaction. Type decimal et non double : aucune imprécision flottante tolérée sur une donnée financière.', json_schema_extra={'unit': 'xof'})
    fee: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)] = Field(description='Frais prélevés.', json_schema_extra={'unit': 'xof'})
    currency: str = Field(default='XOF', description='Code ISO 4217.')
    status: TransactionStatus = Field(description='Issue de la transaction. BLOCKED indique un rejet par les contrôles anti-fraude.')
    failure_reason: str | None = Field(default=None, description="Motif normalisé en cas d'échec (solde insuffisant, compte suspendu, limite dépassée...).")
    channel: TransactionChannel = Field(description="Canal d'initiation. Un changement brutal de canal habituel est un signal de fraude.")
    agent_id: str | None = Field(default=None, description='Agent Mobile Money pour les opérations CASH_IN / CASH_OUT.')
    location_cell_id: str | None = Field(default=None, description="Cellule d'où la transaction a été initiée. GÉNÉRALISATION de la localisation GPS (politique PII §3.2) : jamais de coordonnées précises.")
    region: str | None = Field(default=None, description='Région dérivée de la cellule. Un écart géographique inhabituel est un signal de fraude majeur.')
    device_id: str | None = Field(default=None, description="Empreinte d'appareil. IDENTIFIANT INDIRECT — tokenisé. Un changement d'appareil précédant une transaction inhabituelle est un signal fort.", json_schema_extra={'pii': 'indirect'})
    sender_balance_before: Annotated[Decimal, Field(max_digits=18, decimal_places=2)] | None = Field(default=None, description='Solde avant opération. Permet de détecter les vidages de compte.', json_schema_extra={'unit': 'xof'})
    is_cross_border: bool = Field(default=False, description='Transaction transfrontalière.')
