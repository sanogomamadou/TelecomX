"""Subscriber traffic streams: calls, data sessions, SMS and USSD.

Every outcome is drawn from the **indicators of the cell the subscriber is on at that
moment** (``ctx.kpi``), not from a fixed percentage. A call is dropped with the cell's
current drop rate, a data session ends in a network failure with its packet loss, a USSD
dialogue is abandoned when its latency climbs. So a degrading cell degrades its calls and
sessions too, without any of this being injected stream by stream.
"""

from __future__ import annotations

from datetime import datetime

from telecom_common.contracts.generated.cdr_v1 import CallDetailRecord
from telecom_common.contracts.generated.data_session_v1 import DataSession
from telecom_common.contracts.generated.sms_v1 import SmsEvent
from telecom_common.contracts.generated.ussd_v1 import UssdEvent
from telecomx_generators.events.context import GenContext
from telecomx_generators.events.ids import call_id, prefixed_id
from telecomx_generators.events.rand import bounded_lognormal, stream_rng
from telecomx_generators.events.streams._common import (
    BUCKET_SECONDS,
    envelope,
    poisson_count,
    stamps_in_bucket,
)

__all__ = ["cdr", "data_sessions", "sms", "ussd"]


def _minute(bucket_start: datetime) -> int:
    return int(bucket_start.timestamp() // BUCKET_SECONDS)


# --- cdr.events -----------------------------------------------------------------

_FAILURE_CAUSES = {
    "FAILED": ("RADIO_LINK_FAILURE", "NO_CHANNEL_AVAILABLE", "NETWORK_CONGESTION"),
    "DROPPED": ("RADIO_LINK_LOST", "HANDOVER_FAILURE", "TRANSPORT_FAILURE"),
    "BUSY": ("USER_BUSY",),
    "NO_ANSWER": ("NO_ANSWER_TIMEOUT",),
    "REJECTED": ("CALL_REJECTED",),
}


def cdr(ctx: GenContext, bucket_start: datetime) -> list[CallDetailRecord]:
    """Voice calls. Mobile-originated records only: one record per call."""
    rng = stream_rng(ctx.config.seed, "cdr", _minute(bucket_start))
    count = poisson_count(ctx, rng, "voice", "cdr", bucket_start)
    events: list[CallDetailRecord] = []
    for position, when in enumerate(stamps_in_bucket(rng, bucket_start, count)):
        caller = ctx.population.pick(rng.random())
        callee = ctx.population.subscriber(rng.randrange(ctx.population.size))
        cell_id = ctx.serving_cell(rng, caller.home_cell, caller.region)
        kpi = ctx.kpi.at(cell_id, when)

        roll = rng.random() * 100
        drop, setup = kpi.drop_rate_pct, kpi.call_setup_failure_pct
        if roll < drop:
            status = "DROPPED"
        elif roll < drop + setup:
            status = "FAILED"
        else:
            other = rng.random()
            status = (
                "BUSY"
                if other < 0.03
                else "NO_ANSWER"
                if other < 0.08
                else "REJECTED"
                if other < 0.09
                else "COMPLETED"
            )

        duration = 0
        if status in {"COMPLETED", "DROPPED"}:
            duration = round(bounded_lognormal(rng, 90, 0.9, 1, 7200))
            if status == "DROPPED":
                duration = max(1, round(duration * rng.uniform(0.1, 0.9)))
        events.append(
            CallDetailRecord(
                **envelope(ctx, rng, when, "oss-mediation-01"),
                call_id=call_id(when, position),
                caller_id=caller.msisdn,
                callee_id=callee.msisdn,
                imsi=caller.imsi,
                imei=caller.imei,
                cell_id=cell_id,
                call_type="MO",
                duration_seconds=duration,
                call_status=status,
                failure_cause=rng.choice(_FAILURE_CAUSES[status])
                if status != "COMPLETED"
                else None,
                is_roaming=rng.random() < 0.01,
                is_international=rng.random() < 0.02,
                charged_amount=round(duration / 60 * 10, 2) if status == "COMPLETED" else 0.0,
                rate_plan=caller.plan,
            )
        )
    return events


# --- data.sessions ---------------------------------------------------------------


def data_sessions(ctx: GenContext, bucket_start: datetime) -> list[DataSession]:
    """Mobile data sessions, timestamped at their end."""
    rng = stream_rng(ctx.config.seed, "data.sessions", _minute(bucket_start))
    count = poisson_count(ctx, rng, "data", "data", bucket_start)
    peaks = {t.technology: t.downlink_mbps for t in ctx.catalog.technologies}
    data_bundles = [
        b.bundle_code for b in ctx.catalog.bundles if b.bundle_type in {"DATA", "MIXED"}
    ]
    events: list[DataSession] = []
    for position, when in enumerate(stamps_in_bucket(rng, bucket_start, count)):
        subscriber = ctx.population.pick(rng.random())
        cell_id = ctx.serving_cell(rng, subscriber.home_cell, subscriber.region)
        cell = ctx.cell(cell_id)
        kpi = ctx.kpi.at(cell_id, when)

        duration = round(bounded_lognormal(rng, 420, 1.0, 5, 86_400))
        typical = peaks[cell.technology] * 0.05 * (1 - 0.5 * kpi.served)
        throughput = max(
            0.001,
            min(
                peaks[cell.technology], typical * rng.lognormvariate(0, 0.5) * kpi.throughput_factor
            ),
        )
        download = round(throughput * duration * rng.uniform(0.03, 0.30) / 8, 3)
        upload = round(download * rng.uniform(0.05, 0.25), 3)

        roll = rng.random()
        network_failure = kpi.packet_loss_pct / 100 * 0.6
        handover_failure = kpi.drop_rate_pct / 100 * 0.2
        if roll < network_failure:
            cause = "NETWORK_FAILURE"
        elif roll < network_failure + handover_failure:
            cause = "HANDOVER_FAILURE"
        elif roll < network_failure + handover_failure + 0.05:
            cause = "QUOTA_EXHAUSTED"
        elif roll < network_failure + handover_failure + 0.11:
            cause = "TIMEOUT"
        elif roll < network_failure + handover_failure + 0.14:
            cause = "USER_DETACH"
        else:
            cause = "NORMAL"

        bundle = rng.choice(data_bundles) if rng.random() < 0.7 else None
        apn_roll = rng.random()
        events.append(
            DataSession(
                **envelope(ctx, rng, when, "oss-mediation-01"),
                session_id=prefixed_id("SES", when, position),
                subscriber_id=subscriber.msisdn,
                imei=subscriber.imei,
                ip_address=f"10.{rng.randrange(256)}.{rng.randrange(256)}.{rng.randrange(1, 255)}",
                cell_id=cell_id,
                technology=cell.technology,
                apn="internet" if apn_roll < 0.90 else "mms" if apn_roll < 0.96 else "iot",
                duration_seconds=duration,
                download_mb=download,
                upload_mb=upload,
                total_volume_mb=download + upload,
                avg_throughput_mbps=round(throughput, 3),
                termination_cause=cause,
                bundle_code=bundle,
                charged_amount=0.0 if bundle else round(download * 2, 2),
                is_roaming=rng.random() < 0.01,
            )
        )
    return events


# --- sms.events -------------------------------------------------------------------

_SMS_TYPES = (("P2P", 0.60), ("A2P", 0.20), ("PROMOTIONAL", 0.08), ("OTP", 0.10), ("SYSTEM", 0.02))


def sms(ctx: GenContext, bucket_start: datetime) -> list[SmsEvent]:
    """SMS metadata. The content of a message is never generated: it is never collected."""
    rng = stream_rng(ctx.config.seed, "sms", _minute(bucket_start))
    count = poisson_count(ctx, rng, "sms", "sms", bucket_start)
    events: list[SmsEvent] = []
    for position, when in enumerate(stamps_in_bucket(rng, bucket_start, count)):
        subscriber = ctx.population.pick(rng.random())
        other = ctx.population.subscriber(rng.randrange(ctx.population.size))
        outbound = rng.random() < 0.65
        kind = rng.choices([k for k, _ in _SMS_TYPES], [w for _, w in _SMS_TYPES])[0]
        cell_id = ctx.serving_cell(rng, subscriber.home_cell, subscriber.region)
        status_roll = rng.random()
        segments = 1 if rng.random() < 0.85 else rng.randint(2, 3)
        events.append(
            SmsEvent(
                **envelope(ctx, rng, when, "oss-mediation-01"),
                message_id=prefixed_id("MSG", when, position),
                sender_id=subscriber.msisdn if outbound else other.msisdn,
                receiver_id=other.msisdn if outbound else subscriber.msisdn,
                cell_id=cell_id,
                direction="MO" if outbound else "MT",
                sms_type=kind,
                status="DELIVERED"
                if status_roll < 0.95
                else "FAILED"
                if status_roll < 0.98
                else "EXPIRED"
                if status_roll < 0.99
                else "PENDING",
                segment_count=segments,
                charged_amount=float(segments * 25) if outbound and kind == "P2P" else 0.0,
                is_international=rng.random() < 0.015,
            )
        )
    return events


# --- ussd.events -------------------------------------------------------------------

_USSD_SERVICES = (
    ("*144#", "BALANCE", 0.30, "144>1"),
    ("*145#", "BUNDLE_PURCHASE", 0.25, "145>2>1"),
    ("*111#", "MOBILE_MONEY", 0.30, "111>3>2"),
    ("*100#", "CUSTOMER_SERVICE", 0.05, "100>4"),
    ("*130#", "PROMOTION", 0.05, "130>1"),
    ("*155#", "TRANSFER", 0.05, "155>2>3"),
)


def ussd(ctx: GenContext, bucket_start: datetime) -> list[UssdEvent]:
    """USSD dialogues. Abandonment and timeouts rise with the cell's latency and losses."""
    rng = stream_rng(ctx.config.seed, "ussd", _minute(bucket_start))
    count = poisson_count(ctx, rng, "ussd", "ussd", bucket_start)
    events: list[UssdEvent] = []
    for position, when in enumerate(stamps_in_bucket(rng, bucket_start, count)):
        subscriber = ctx.population.pick(rng.random())
        cell_id = ctx.serving_cell(rng, subscriber.home_cell, subscriber.region)
        kpi = ctx.kpi.at(cell_id, when)
        code, category, _, path = rng.choices(_USSD_SERVICES, [s[2] for s in _USSD_SERVICES])[0]

        abandon = min(0.45, 0.04 + max(0.0, kpi.latency_ms - 35) / 1500)
        timeout = 0.02 + kpi.packet_loss_pct / 200
        network_error = kpi.drop_rate_pct / 200
        roll = rng.random()
        if roll < abandon:
            status = "ABANDONED"
        elif roll < abandon + timeout:
            status = "TIMEOUT"
        elif roll < abandon + timeout + network_error:
            status = "NETWORK_ERROR"
        elif roll < abandon + timeout + network_error + 0.01:
            status = "FAILED"
        else:
            status = "COMPLETED"
        events.append(
            UssdEvent(
                **envelope(ctx, rng, when, "oss-mediation-01"),
                session_id=prefixed_id("USS", when, position),
                subscriber_id=subscriber.msisdn,
                service_code=code,
                menu_path=path,
                service_category=category,
                cell_id=cell_id,
                duration_seconds=round(bounded_lognormal(rng, 25, 0.6, 2, 600)),
                step_count=rng.randint(1, 6),
                status=status,
            )
        )
    return events
