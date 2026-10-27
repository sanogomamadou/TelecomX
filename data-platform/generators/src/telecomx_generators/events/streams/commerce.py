"""Commercial streams: recharges, offers and Mobile Money (with fraud injection)."""

from __future__ import annotations

from datetime import datetime, timedelta

from telecom_common.contracts.generated.bundle_v1 import BundleEvent
from telecom_common.contracts.generated.mobilemoney_transaction_v1 import MobileMoneyTransaction
from telecom_common.contracts.generated.recharge_v1 import RechargeEvent
from telecomx_generators.events.context import GenContext
from telecomx_generators.events.ids import prefixed_id, transaction_id
from telecomx_generators.events.rand import bounded_lognormal, stream_rng
from telecomx_generators.events.scenarios import FraudBurst
from telecomx_generators.events.streams._common import (
    BUCKET_SECONDS,
    envelope,
    money,
    poisson_count,
    stamps_in_bucket,
)

__all__ = ["bundles", "mobile_money", "recharges"]


def _minute(bucket_start: datetime) -> int:
    return int(bucket_start.timestamp() // BUCKET_SECONDS)


# --- recharge.events -----------------------------------------------------------------

_AMOUNTS = (100, 200, 500, 1000, 2000, 5000, 10000)
_AMOUNT_WEIGHTS = {
    "light": (0.20, 0.25, 0.30, 0.15, 0.07, 0.03, 0.00),
    "standard": (0.08, 0.14, 0.26, 0.26, 0.14, 0.09, 0.03),
    "heavy": (0.02, 0.04, 0.12, 0.22, 0.26, 0.22, 0.12),
}
_RECHARGE_CHANNELS = (
    ("SCRATCH_CARD", 0.25),
    ("MOBILE_MONEY", 0.35),
    ("AGENT", 0.25),
    ("BANK", 0.02),
    ("APP", 0.05),
    ("USSD", 0.07),
    ("ATM", 0.01),
)


def recharges(ctx: GenContext, bucket_start: datetime) -> list[RechargeEvent]:
    """Prepaid recharges. The amount depends on the subscriber's segment."""
    rng = stream_rng(ctx.config.seed, "recharge", _minute(bucket_start))
    count = poisson_count(ctx, rng, "recharge", "recharge", bucket_start)
    events: list[RechargeEvent] = []
    for position, when in enumerate(stamps_in_bucket(rng, bucket_start, count)):
        subscriber = ctx.population.pick(rng.random())
        amount = rng.choices(_AMOUNTS, _AMOUNT_WEIGHTS[subscriber.segment])[0]
        channel = rng.choices(
            [c for c, _ in _RECHARGE_CHANNELS], [w for _, w in _RECHARGE_CHANNELS]
        )[0]
        roll = rng.random()
        events.append(
            RechargeEvent(
                **envelope(ctx, rng, when, "billing-01"),
                recharge_id=prefixed_id("RCH", when, position),
                subscriber_id=subscriber.msisdn,
                amount=money(amount),
                channel=channel,
                agent_id=f"AGT-{rng.randrange(1, 3000):05d}" if channel == "AGENT" else None,
                balance_after=money(amount + rng.uniform(0, 3000)),
                status="SUCCESS"
                if roll < 0.985
                else "FAILED"
                if roll < 0.995
                else "PENDING"
                if roll < 0.999
                else "REVERSED",
                region=subscriber.region,
                is_first_recharge=rng.random() < 0.005,
            )
        )
    return events


# --- bundle.events -------------------------------------------------------------------

_BUNDLE_EVENTS = (
    ("PURCHASED", 0.40),
    ("ACTIVATED", 0.25),
    ("CONSUMED", 0.10),
    ("EXPIRED", 0.12),
    ("RENEWED", 0.10),
    ("CANCELLED", 0.03),
)
_PURCHASE_CHANNELS = (("USSD", 0.50), ("APP", 0.20), ("AGENT", 0.15), ("WEB", 0.05), ("SMS", 0.02))


def bundles(ctx: GenContext, bucket_start: datetime) -> list[BundleEvent]:
    """Offer lifecycle events.

    Limitation, stated rather than hidden: each event is drawn independently, so there is
    no state linking the purchase, the consumption and the expiry of one given offer. An
    ``EXPIRED`` event does not follow a real ``PURCHASED`` event of the same subscriber.
    The lifecycle is plausible in aggregate, not per offer.
    """
    rng = stream_rng(ctx.config.seed, "bundle", _minute(bucket_start))
    count = poisson_count(ctx, rng, "recharge", "bundle", bucket_start)
    offers = ctx.catalog.bundles
    popularity = [1 / (float(b.price_xof) ** 0.5) for b in offers]
    events: list[BundleEvent] = []
    for when in stamps_in_bucket(rng, bucket_start, count):
        subscriber = ctx.population.pick(rng.random())
        offer = rng.choices(offers, popularity)[0]
        kind = rng.choices([k for k, _ in _BUNDLE_EVENTS], [w for _, w in _BUNDLE_EVENTS])[0]

        consumed = None
        if offer.allowance_mb is not None and kind in {"CONSUMED", "EXPIRED"}:
            share = rng.random() if kind == "CONSUMED" else rng.betavariate(1, 3)
            consumed = round(offer.allowance_mb * share, 2)
        channel = (
            "AUTO_RENEWAL"
            if kind == "RENEWED"
            else rng.choices(
                [c for c, _ in _PURCHASE_CHANNELS], [w for _, w in _PURCHASE_CHANNELS]
            )[0]
        )
        events.append(
            BundleEvent(
                **envelope(ctx, rng, when, "billing-01"),
                subscriber_id=subscriber.msisdn,
                bundle_code=offer.bundle_code,
                event_type=kind,
                bundle_type=offer.bundle_type,
                price=offer.price_xof,
                allowance_mb=offer.allowance_mb,
                allowance_minutes=offer.allowance_minutes,
                consumed_mb=consumed,
                validity_days=offer.validity_days,
                expiry_timestamp=(
                    when + timedelta(days=offer.validity_days)
                    if kind in {"PURCHASED", "ACTIVATED", "RENEWED"}
                    else None
                ),
                purchase_channel=channel,
                is_auto_renew=kind == "RENEWED" or rng.random() < 0.05,
            )
        )
    return events


# --- mobilemoney.transactions ----------------------------------------------------------

_MOMO_TYPES = (
    ("P2P", 0.35, 1.0),
    ("CASH_IN", 0.20, 1.5),
    ("CASH_OUT", 0.20, 1.2),
    ("MERCHANT_PAYMENT", 0.10, 0.6),
    ("BILL_PAYMENT", 0.05, 0.8),
    ("AIRTIME_PURCHASE", 0.08, 0.15),
    ("BULK_DISBURSEMENT", 0.01, 8.0),
    ("REVERSAL", 0.01, 1.0),
)
_MOMO_CHANNELS = (("USSD", 0.50), ("APP", 0.25), ("AGENT", 0.20), ("API", 0.03), ("WEB", 0.02))
_FAILURE_REASONS = ("INSUFFICIENT_BALANCE", "LIMIT_EXCEEDED", "RECEIVER_UNREACHABLE", "TIMEOUT")


def _fee(kind: str, amount: float) -> float:
    if kind in {"CASH_IN", "AIRTIME_PURCHASE"}:
        return 0.0
    return max(25.0, round(amount * 0.01, 0))


def mobile_money(ctx: GenContext, bucket_start: datetime) -> list[MobileMoneyTransaction]:
    """Background Mobile Money transactions, plus those of any fraud burst."""
    minute = _minute(bucket_start)
    rng = stream_rng(ctx.config.seed, "momo", minute)
    count = poisson_count(ctx, rng, "momo", "momo", bucket_start)
    events: list[MobileMoneyTransaction] = []
    for position, when in enumerate(stamps_in_bucket(rng, bucket_start, count)):
        sender = ctx.population.pick(rng.random())
        kind, _, factor = rng.choices(_MOMO_TYPES, [t[1] for t in _MOMO_TYPES])[0]
        amount = (
            round(bounded_lognormal(rng, sender.typical_amount * factor, 0.8, 100, 2_000_000) / 5)
            * 5
        )
        cell_id = ctx.serving_cell(rng, sender.home_cell, sender.region)
        agent = f"AGT-{rng.randrange(1, 3000):05d}"

        merchant = None
        if kind == "MERCHANT_PAYMENT":
            merchant = f"MCH-{rng.randrange(1, 42_000):05d}"
            receiver = f"+22308{rng.randrange(10**6):06d}"
        elif kind in {"CASH_IN", "CASH_OUT"}:
            receiver = f"+22309{rng.randrange(10**6):06d}"
        else:
            receiver = ctx.population.subscriber(rng.randrange(ctx.population.size)).msisdn
        roll = rng.random()
        status = (
            "COMPLETED"
            if roll < 0.96
            else "FAILED"
            if roll < 0.985
            else "PENDING"
            if roll < 0.995
            else "REVERSED"
            if roll < 0.998
            else "BLOCKED"
        )
        channel = (
            "AGENT"
            if kind in {"CASH_IN", "CASH_OUT"} and rng.random() < 0.8
            else rng.choices([c for c, _ in _MOMO_CHANNELS], [w for _, w in _MOMO_CHANNELS])[0]
        )
        events.append(
            MobileMoneyTransaction(
                **envelope(ctx, rng, when, "momo-platform-01"),
                transaction_id=transaction_id(when, position),
                transaction_type=kind,
                sender_account=sender.msisdn,
                receiver_account=receiver,
                merchant_id=merchant,
                amount=money(amount),
                fee=money(_fee(kind, amount)),
                status=status,
                failure_reason=rng.choice(_FAILURE_REASONS) if status == "FAILED" else None,
                channel=channel,
                agent_id=agent if channel == "AGENT" else None,
                location_cell_id=cell_id,
                region=ctx.region_of_cell(cell_id),
                device_id=sender.imei,
                sender_balance_before=money(amount * rng.uniform(1.05, 40)),
                is_cross_border=kind == "P2P" and rng.random() < 0.01,
            )
        )
    for burst in ctx.frauds:
        events.extend(_fraud_transactions(ctx, burst, bucket_start))
    return events


def _fraud_transactions(
    ctx: GenContext, burst: FraudBurst, bucket_start: datetime
) -> list[MobileMoneyTransaction]:
    """The transactions of a takeover that fall in this bucket.

    Eight small-to-medium transfers to fresh counterparties, from a device the account has
    never used and a region far from its owner's, then one large withdrawal.
    """
    account = ctx.population.subscriber(burst.subscriber_index)
    rng = stream_rng(ctx.config.seed, "fraud", burst.subscriber_index, _minute(bucket_start))
    end = bucket_start + timedelta(seconds=BUCKET_SECONDS)
    events: list[MobileMoneyTransaction] = []
    last = burst.transactions - 1
    for i in range(burst.transactions):
        when = burst.start + timedelta(minutes=burst.span_minutes * i / max(1, last))
        if not bucket_start <= when < end:
            continue
        is_final = i == last
        amount = (
            burst.final_amount
            if is_final
            else round(5_000 + 115_000 * (i / max(1, last - 1)) ** 2, -2)
        )
        cell_id = ctx.population.cell_in_region(burst.destination_region, rng.random())
        txn = transaction_id(when, 9_000 + i)
        events.append(
            MobileMoneyTransaction(
                **envelope(ctx, rng, when, "momo-platform-01"),
                transaction_id=txn,
                transaction_type="CASH_OUT" if is_final else "P2P",
                sender_account=account.msisdn,
                receiver_account=f"+22309{900_000 + burst.subscriber_index * 10 + i:06d}",
                merchant_id=None,
                amount=money(amount),
                fee=money(_fee("CASH_OUT", amount)),
                status="COMPLETED",
                failure_reason=None,
                channel="AGENT" if is_final else "APP",
                agent_id=f"AGT-{rng.randrange(1, 3000):05d}" if is_final else None,
                location_cell_id=cell_id,
                region=ctx.region_of_cell(cell_id),
                device_id=f"35{rng.randrange(10**13):013d}",  # never seen on this account
                sender_balance_before=money(
                    1_200_000 - sum(5_000 + 115_000 * (j / max(1, last - 1)) ** 2 for j in range(i))
                ),
                is_cross_border=False,
            )
        )
        ctx.truth.record(
            kind="fraud_transaction",
            scenario="fraud_burst",
            transaction_id=txn,
            subscriber_index=burst.subscriber_index,
            amount=amount,
            at=when.isoformat(),
        )
    return events
