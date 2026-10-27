"""Customer relationship events, including the human echo of a network incident."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from telecom_common.contracts.generated.customer_event_v1 import CustomerEvent
from telecomx_generators.events.context import GenContext
from telecomx_generators.events.population import Subscriber
from telecomx_generators.events.rand import bounded_lognormal, poisson, stream_rng
from telecomx_generators.events.streams._common import (
    BUCKET_SECONDS,
    envelope,
    poisson_count,
    stamps_in_bucket,
)

__all__ = ["customer_events"]

_TYPES = (
    ("SUPPORT_CONTACT", 0.30),
    ("COMPLAINT", 0.20),
    ("SATISFACTION_SURVEY", 0.13),
    ("PLAN_CHANGE", 0.10),
    ("ACTIVATION", 0.10),
    ("DEVICE_CHANGE", 0.05),
    ("SUSPENSION", 0.03),
    ("REACTIVATION", 0.03),
    ("CHURN", 0.02),
    ("PORT_OUT_REQUEST", 0.02),
    ("SIM_SWAP", 0.02),
)
_CATEGORIES = (
    ("NETWORK_QUALITY", 0.30),
    ("BILLING", 0.30),
    ("BUNDLE", 0.15),
    ("MOBILE_MONEY", 0.10),
    ("DEVICE", 0.05),
    ("OTHER", 0.10),
)
_CHANNELS = ("CALL_CENTER", "SHOP", "APP", "USSD", "SMS", "WEB")
_PLANS = ("PREPAID-BASIC", "PREPAID-PLUS", "PREPAID-DATA", "POSTPAID-PRO")

#: Generic, neutral wording. Free text from a CRM is untrusted data (threat model 5.2):
#: nothing here tries to look like an instruction.
_DESCRIPTIONS = {
    "NETWORK_QUALITY": "Appels coupés et connexion lente dans la zone depuis plusieurs heures.",
    "BILLING": "Débit inattendu sur le compte après un achat de forfait.",
    "BUNDLE": "Forfait acheté mais volume non crédité.",
    "MOBILE_MONEY": "Transaction Mobile Money en attente depuis la veille.",
    "DEVICE": "Terminal non reconnu après remplacement de la carte SIM.",
    "OTHER": "Demande d'information sur les offres disponibles.",
}


def _event(
    ctx: GenContext,
    rng: random.Random,
    when: datetime,
    subscriber: Subscriber,
    kind: str,
    *,
    category: str | None = None,
    related_cell: str | None = None,
) -> CustomerEvent:
    is_complaint = kind in {"COMPLAINT", "SUPPORT_CONTACT"}
    resolved = is_complaint and rng.random() < 0.8
    new_plan = (
        rng.choice([p for p in _PLANS if p != subscriber.plan]) if kind == "PLAN_CHANGE" else None
    )
    return CustomerEvent(
        **envelope(ctx, rng, when, "crm-01"),
        subscriber_id=subscriber.msisdn,
        event_type=kind,
        channel=rng.choice(_CHANNELS),
        category=category,
        description=_DESCRIPTIONS.get(category or "") if kind == "COMPLAINT" else None,
        severity=rng.choices(["LOW", "MEDIUM", "HIGH", "CRITICAL"], [4, 4, 2, 0.5])[0]
        if kind == "COMPLAINT"
        else None,
        resolution_status=(
            "RESOLVED" if resolved else rng.choice(["OPEN", "IN_PROGRESS", "ESCALATED"])
        )
        if is_complaint
        else None,
        resolution_time_minutes=round(bounded_lognormal(rng, 120, 1.0, 5, 10_080))
        if resolved
        else None,
        satisfaction_score=rng.randint(0, 10) if kind == "SATISFACTION_SURVEY" else None,
        related_cell_id=related_cell,
        region=subscriber.region,
        previous_plan=subscriber.plan if kind == "PLAN_CHANGE" else None,
        new_plan=new_plan,
    )


def customer_events(ctx: GenContext, bucket_start: datetime) -> list[CustomerEvent]:
    """Background CRM activity, network-quality complaints during a degradation, and the
    SIM swap that precedes a Mobile Money takeover."""
    minute = int(bucket_start.timestamp() // BUCKET_SECONDS)
    rng = stream_rng(ctx.config.seed, "customer", minute)
    events: list[CustomerEvent] = []

    for when in stamps_in_bucket(
        rng, bucket_start, poisson_count(ctx, rng, "customer", "customer", bucket_start)
    ):
        subscriber = ctx.population.pick(rng.random())
        kind = rng.choices([k for k, _ in _TYPES], [w for _, w in _TYPES])[0]
        category = (
            rng.choices([c for c, _ in _CATEGORIES], [w for _, w in _CATEGORIES])[0]
            if kind == "COMPLAINT"
            else (rng.choice(["BILLING", "BUNDLE", "OTHER"]) if kind == "SUPPORT_CONTACT" else None)
        )
        related = subscriber.home_cell if category == "NETWORK_QUALITY" else None
        events.append(
            _event(ctx, rng, when, subscriber, kind, category=category, related_cell=related)
        )

    # Complaints caused by a degradation. They are NOT thinned by speed_factor: the
    # scenario must stay visible whatever the background volume.
    scenario_rng = stream_rng(ctx.config.seed, "customer.scenario", minute)
    for scenario in ctx.degradations:
        weight = ctx.kpi.at(scenario.cell_id, bucket_start).degradation
        affected = ctx.subscribers_on_cell(scenario.cell_id)
        if weight <= 0 or not affected:
            continue
        count = poisson(scenario_rng, 0.8 * weight)
        for when in stamps_in_bucket(scenario_rng, bucket_start, count):
            subscriber = ctx.population.subscriber(scenario_rng.choice(affected))
            events.append(
                _event(
                    ctx,
                    scenario_rng,
                    when,
                    subscriber,
                    "COMPLAINT",
                    category="NETWORK_QUALITY",
                    related_cell=scenario.cell_id,
                )
            )

    # A takeover starts with a SIM swap, shortly before the transactions.
    for burst in ctx.frauds:
        swap_time = burst.start - timedelta(minutes=burst.sim_swap_lead_minutes)
        if bucket_start <= swap_time < bucket_start + timedelta(seconds=BUCKET_SECONDS):
            subscriber = ctx.population.subscriber(burst.subscriber_index)
            events.append(_event(ctx, scenario_rng, swap_time, subscriber, "SIM_SWAP"))
            ctx.truth.record(
                kind="sim_swap",
                scenario="fraud_burst",
                subscriber_index=burst.subscriber_index,
                at=swap_time.isoformat(),
            )
    return events
