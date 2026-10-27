"""Tests for the synthetic event generators (lot 1.4)."""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from telecom_common.contracts.generated import BY_TOPIC, MODELS
from telecom_common.contracts.manifest import load_manifest
from telecom_common.reference import ReferenceCatalog
from telecomx_generators.events import (
    CellDegradation,
    Emitted,
    FraudBurst,
    GenContext,
    GeneratorConfig,
    cli,
    generate,
    generate_topic,
)
from telecomx_generators.events.ids import alarm_id, call_id, transaction_id, uuid7
from telecomx_generators.events.rand import bounded_lognormal, poisson, stream_rng
from telecomx_generators.events.seasonality import KINDS, load_factor
from telecomx_generators.events.sinks import JsonlSink, MemorySink
from telecomx_generators.events.streams import STREAMS
from telecomx_generators.reference.build import DEFAULT_SEED, build_catalog

pytestmark = pytest.mark.unit

START = datetime(2026, 9, 1, 17, 30, tzinfo=UTC)
DEGRADATION_START = datetime(2026, 9, 1, 17, 45, tzinfo=UTC)
FRAUD_START = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=120)
FRAUD_INDEX = 5


@pytest.fixture(scope="module")
def catalog() -> ReferenceCatalog:
    return build_catalog(DEFAULT_SEED)


@pytest.fixture(scope="module")
def tenant(catalog: ReferenceCatalog) -> str:
    return catalog.tenants[0].tenant_id


def _context(catalog: ReferenceCatalog, tenant: str, *, scenarios: bool = True) -> GenContext:
    chosen = (
        (
            CellDegradation("CELL-0421", "SITE-102", start=DEGRADATION_START),
            FraudBurst(FRAUD_INDEX, start=FRAUD_START),
        )
        if scenarios
        else ()
    )
    return GenContext(catalog, tenant, GeneratorConfig(), chosen)


@pytest.fixture(scope="module")
def ctx(catalog: ReferenceCatalog, tenant: str) -> GenContext:
    return _context(catalog, tenant)


@pytest.fixture(scope="module")
def emitted(ctx: GenContext) -> list[Emitted]:
    return generate(ctx, START, START + WINDOW)


def _of(items: Iterable[Emitted], topic: str) -> list:
    return [e.event for e in items if e.topic == topic]


# -- Primitives ----------------------------------------------------------------------


class TestPrimitives:
    def test_streams_are_independent_and_reproducible(self) -> None:
        assert stream_rng(1, "a", 2).random() == stream_rng(1, "a", 2).random()
        assert stream_rng(1, "a", 2).random() != stream_rng(1, "a", 3).random()
        assert stream_rng(1, "a", 2).random() != stream_rng(2, "a", 2).random()

    @pytest.mark.parametrize("lam", [0.0, 0.5, 5.0, 80.0])
    def test_poisson_mean_matches_lambda(self, lam: float) -> None:
        rng = random.Random(7)
        draws = [poisson(rng, lam) for _ in range(4000)]
        assert min(draws) >= 0
        assert sum(draws) / len(draws) == pytest.approx(lam, abs=max(0.1, lam * 0.06))

    def test_bounded_lognormal_respects_bounds(self) -> None:
        rng = random.Random(3)
        values = [bounded_lognormal(rng, 100, 2.0, 10, 500) for _ in range(2000)]
        assert min(values) >= 10
        assert max(values) <= 500

    def test_uuid7_is_valid_sortable_and_reproducible(self) -> None:
        early = uuid7(random.Random(1), START)
        late = uuid7(random.Random(1), START + timedelta(seconds=1))
        assert early == uuid7(random.Random(1), START)
        assert early[14] == "7"
        assert early < late

    def test_business_ids_are_unique_per_position_and_minute(self) -> None:
        a, b = START, START + timedelta(minutes=1)
        ids = {call_id(w, p) for w in (a, b) for p in range(5)}
        ids |= {alarm_id(w, p) for w in (a, b) for p in range(5)}
        ids |= {transaction_id(w, p) for w in (a, b) for p in range(5)}
        assert len(ids) == 30

    @pytest.mark.parametrize("kind", KINDS)
    def test_load_factor_is_positive_and_averages_to_one_over_a_day(self, kind: str) -> None:
        day = datetime(2026, 9, 9, tzinfo=UTC)  # a Wednesday, neither a month start nor an event
        factors = [load_factor(kind, day + timedelta(minutes=m)) for m in range(0, 1440, 5)]
        assert min(factors) > 0
        assert sum(factors) / len(factors) == pytest.approx(1.0, rel=0.35)

    def test_evening_peak_exceeds_night_for_voice(self) -> None:
        peak = load_factor("voice", datetime(2026, 9, 9, 18, tzinfo=UTC))
        night = load_factor("voice", datetime(2026, 9, 9, 3, tzinfo=UTC))
        assert peak > 3 * night

    def test_new_year_boosts_sms(self) -> None:
        new_year = load_factor("sms", datetime(2027, 1, 1, 0, 5, tzinfo=UTC))
        ordinary = load_factor("sms", datetime(2027, 1, 15, 0, 5, tzinfo=UTC))
        assert new_year > 10 * ordinary


class TestPopulation:
    def test_a_subscriber_is_always_the_same_person(self, ctx: GenContext) -> None:
        assert ctx.population.subscriber(42) == ctx.population.subscriber(42)
        assert ctx.population.subscriber(42) != ctx.population.subscriber(43)

    def test_identifiers_cannot_belong_to_a_real_subscriber(self, ctx: GenContext) -> None:
        person = ctx.population.subscriber(7)
        assert person.msisdn.startswith("+2230")
        assert person.imsi.startswith("61099")

    @pytest.mark.parametrize("index", [-1, 20_000])
    def test_index_outside_the_population_is_refused(self, ctx: GenContext, index: int) -> None:
        with pytest.raises(IndexError):
            ctx.population.subscriber(index)

    def test_home_cells_belong_to_the_subscribers_region(self, ctx: GenContext) -> None:
        person = ctx.population.subscriber(11)
        assert person.home_cell in ctx.population.cells_of(person.region)

    def test_unknown_seasonality_kind_is_refused(self) -> None:
        with pytest.raises(KeyError):
            load_factor("telepathy", START)


class TestConfig:
    def test_defaults_are_valid(self) -> None:
        assert GeneratorConfig().lookback_seconds == 960

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"speed_factor": 0},
            {"speed_factor": 1.5},
            {"periodic_fraction": 0},
            {"population_size": 10},
            {"late_fraction": 1.2},
            {"very_late_fraction": -0.1},
            {"max_delay_seconds": 0},
            {"max_delay_seconds": 600, "very_late_max_seconds": 300},
        ],
    )
    def test_invalid_values_are_rejected(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            GeneratorConfig(**kwargs)


# -- Validity of what is generated ---------------------------------------------------


class TestEveryTopic:
    def test_all_eleven_topics_produce_events(self, emitted: list[Emitted]) -> None:
        counts = Counter(e.topic for e in emitted)
        assert set(counts) == set(BY_TOPIC) == set(STREAMS)
        assert all(n > 0 for n in counts.values()), counts

    def test_events_are_instances_of_the_topic_contract(self, emitted: list[Emitted]) -> None:
        assert all(isinstance(e.event, BY_TOPIC[e.topic]) for e in emitted)

    def test_events_respect_the_catalogue(
        self, emitted: list[Emitted], catalog: ReferenceCatalog
    ) -> None:
        manifest = load_manifest()
        spec_of = {
            t: next(s for k, s in manifest.items() if MODELS[k] is m) for t, m in BY_TOPIC.items()
        }
        problems = [
            (e.topic, p)
            for e in emitted
            for p in catalog.check_record(spec_of[e.topic], e.event.model_dump())
        ]
        assert problems == []

    def test_event_ids_are_unique(self, emitted: list[Emitted]) -> None:
        ids = [e.event.event_id for e in emitted]
        assert len(ids) == len(set(ids))

    def test_output_is_ordered_by_arrival(self, emitted: list[Emitted]) -> None:
        arrivals = [e.arrival for e in emitted]
        assert arrivals == sorted(arrivals)

    def test_everything_arrives_inside_the_window(self, emitted: list[Emitted]) -> None:
        assert all(START <= e.arrival < START + WINDOW for e in emitted)

    def test_no_event_precedes_its_own_arrival(self, emitted: list[Emitted]) -> None:
        assert all(e.event.event_timestamp <= e.event.ingestion_timestamp for e in emitted)

    def test_nothing_is_generated_in_the_future_of_the_window(self, emitted: list[Emitted]) -> None:
        assert max(e.event.event_timestamp for e in emitted) < START + WINDOW

    def test_all_events_belong_to_the_tenant(self, emitted: list[Emitted], tenant: str) -> None:
        assert {e.event.tenant_id for e in emitted} == {tenant}

    def test_sms_never_carries_message_content(self, emitted: list[Emitted]) -> None:
        sms = _of(emitted, "sms.events")
        assert sms
        assert not {"content", "text", "body"} & set(type(sms[0]).model_fields)


# -- Determinism ---------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_gives_identical_events(
        self, catalog: ReferenceCatalog, tenant: str, emitted: list[Emitted]
    ) -> None:
        again = generate(_context(catalog, tenant), START, START + WINDOW)
        assert [e.event.model_dump_json() for e in again] == [
            e.event.model_dump_json() for e in emitted
        ]

    def test_another_seed_gives_other_events(self, catalog: ReferenceCatalog, tenant: str) -> None:
        other = GenContext(catalog, tenant, GeneratorConfig(seed=7), ())
        a = generate_topic(other, "cdr.events", START, START + timedelta(minutes=10))
        base = _context(catalog, tenant, scenarios=False)
        b = generate_topic(base, "cdr.events", START, START + timedelta(minutes=10))
        assert [e.event_id for e in a] != [e.event_id for e in b]

    @pytest.mark.parametrize("topic", sorted(STREAMS))
    def test_window_decomposition_does_not_change_events(self, ctx: GenContext, topic: str) -> None:
        """Cutting a period into windows differently yields the same events."""
        end = START + timedelta(minutes=40)
        whole = generate_topic(ctx, topic, START, end)
        cut = START + timedelta(minutes=17, seconds=13)
        parts = generate_topic(ctx, topic, START, cut) + generate_topic(ctx, topic, cut, end)
        assert sorted(e.event_id for e in whole) == sorted(e.event_id for e in parts)

    def test_regenerating_a_window_does_not_duplicate_ground_truth(
        self, catalog: ReferenceCatalog, tenant: str
    ) -> None:
        fresh = _context(catalog, tenant)
        for _ in range(2):
            generate_topic(
                fresh, "mobilemoney.transactions", FRAUD_START, FRAUD_START + timedelta(minutes=30)
            )
        assert len(fresh.truth.events) == 9


# -- Volume, seasonality, lateness ---------------------------------------------------


class TestVolumeAndLateness:
    def test_speed_factor_scales_transactional_volume(
        self, catalog: ReferenceCatalog, tenant: str
    ) -> None:
        def volume(factor: float) -> int:
            context = GenContext(catalog, tenant, GeneratorConfig(speed_factor=factor), ())
            return len(generate_topic(context, "cdr.events", START, START + timedelta(minutes=30)))

        assert volume(0.002) == pytest.approx(2 * volume(0.001), rel=0.15)

    def test_periodic_streams_ignore_speed_factor(
        self, catalog: ReferenceCatalog, tenant: str
    ) -> None:
        def volume(factor: float) -> int:
            context = GenContext(catalog, tenant, GeneratorConfig(speed_factor=factor), ())
            return len(
                generate_topic(context, "network.metrics", START, START + timedelta(minutes=10))
            )

        assert volume(0.001) == volume(0.01)

    def test_periodic_streams_cover_the_demonstration_cells(self, ctx: GenContext) -> None:
        assert {"CELL-0421", "CELL-0721"} <= set(ctx.periodic_cells)
        assert "ROUTER-082" in ctx.periodic_equipment

    def test_night_is_quieter_than_the_evening_peak(self, ctx: GenContext) -> None:
        night = generate_topic(
            ctx,
            "cdr.events",
            datetime(2026, 9, 2, 3, tzinfo=UTC),
            datetime(2026, 9, 2, 4, tzinfo=UTC),
        )
        evening = generate_topic(
            ctx,
            "cdr.events",
            datetime(2026, 9, 2, 18, tzinfo=UTC),
            datetime(2026, 9, 2, 19, tzinfo=UTC),
        )
        assert len(evening) > 3 * len(night)

    def test_about_two_percent_of_events_arrive_late(self, emitted: list[Emitted]) -> None:
        delays = [(e.arrival - e.event.event_timestamp).total_seconds() for e in emitted]
        late = sum(d > 5 for d in delays) / len(delays)
        assert 0.012 < late < 0.035

    def test_very_late_events_exceed_the_watermark(self, ctx: GenContext) -> None:
        wide = generate_topic(ctx, "cdr.events", START, START + timedelta(hours=3))
        delays = [(e.ingestion_timestamp - e.event_timestamp).total_seconds() for e in wide]
        assert max(delays) > 300
        assert max(delays) <= 900


# -- The scenarios -------------------------------------------------------------------


class TestDegradationScenario:
    def test_packet_loss_climbs_from_baseline_to_the_prd_level(
        self, emitted: list[Emitted]
    ) -> None:
        series = {
            m.event_timestamp: m.packet_loss_pct
            for m in _of(emitted, "network.metrics")
            if m.cell_id == "CELL-0421"
        }
        before = [v for t, v in series.items() if t < DEGRADATION_START]
        plateau = [v for t, v in series.items() if t > DEGRADATION_START + timedelta(minutes=60)]
        assert sum(before) / len(before) < 2.0
        assert sum(plateau) / len(plateau) == pytest.approx(14.8, abs=2.0)

    def test_the_degradation_reaches_other_streams(self, emitted: list[Emitted]) -> None:
        def failure_rate(window_start: datetime, window_end: datetime) -> float:
            calls = [
                c
                for c in _of(emitted, "cdr.events")
                if c.cell_id == "CELL-0421" and window_start <= c.event_timestamp < window_end
            ]
            bad = [c for c in calls if c.call_status.value in {"DROPPED", "FAILED"}]
            return len(bad) / max(1, len(calls))

        healthy = failure_rate(START, DEGRADATION_START)
        degraded = failure_rate(DEGRADATION_START + timedelta(minutes=60), START + WINDOW)
        assert degraded > 3 * max(healthy, 0.01)

    def test_a_clean_run_has_no_degradation(self, catalog: ReferenceCatalog, tenant: str) -> None:
        clean = _context(catalog, tenant, scenarios=False)
        events = generate_topic(
            clean, "network.metrics", START + timedelta(minutes=70), START + WINDOW
        )
        losses = [m.packet_loss_pct for m in events if m.cell_id == "CELL-0421"]
        assert losses
        assert max(losses) < 5.0

    def test_router_shows_the_backhaul_saturation(self, emitted: list[Emitted]) -> None:
        router = [t for t in _of(emitted, "device.events") if t.equipment_id == "ROUTER-082"]
        early = [t.cpu_utilization_pct for t in router if t.event_timestamp < DEGRADATION_START]
        late = [
            t.cpu_utilization_pct
            for t in router
            if t.event_timestamp > DEGRADATION_START + timedelta(minutes=60)
        ]
        assert early and late
        assert sum(late) / len(late) > sum(early) / len(early) + 20

    def test_customers_complain_about_the_cell(self, emitted: list[Emitted]) -> None:
        complaints = [
            c for c in _of(emitted, "customer.events") if c.related_cell_id == "CELL-0421"
        ]
        assert len(complaints) >= 20
        assert all(c.event_type.value == "COMPLAINT" for c in complaints)
        assert min(c.event_timestamp for c in complaints) >= DEGRADATION_START

    def test_the_incident_raises_correlated_alarms(self, emitted: list[Emitted]) -> None:
        raised = [
            a
            for a in _of(emitted, "network.alarms")
            if (a.correlation_id or "").startswith("CORR-CELL-0421")
        ]
        assert {a.alarm_code for a in raised} >= {"PACKET_LOSS_HIGH", "BACKHAUL_CONGESTION"}
        assert {a.correlation_id for a in raised} == {"CORR-CELL-0421-202609011745"}
        transport = [a for a in raised if a.alarm_code == "BACKHAUL_CONGESTION"]
        assert {a.equipment_id for a in transport} == {"ROUTER-082"}

    def test_a_resolved_incident_clears_each_alarm_it_opened(
        self, catalog: ReferenceCatalog, tenant: str
    ) -> None:
        resolved = CellDegradation(
            "CELL-0421",
            "SITE-102",
            start=DEGRADATION_START,
            resolved_at=DEGRADATION_START + timedelta(hours=1),
        )
        context = GenContext(catalog, tenant, GeneratorConfig(), (resolved,))
        alarms = [
            a
            for a in generate_topic(context, "network.alarms", START, START + timedelta(hours=4))
            if (a.correlation_id or "").startswith("CORR-")
        ]
        by_code: dict[str, list] = {}
        for alarm in alarms:
            by_code.setdefault(alarm.alarm_code, []).append(alarm)
        assert by_code
        for code, pair in by_code.items():
            statuses = [a.status.value for a in sorted(pair, key=lambda a: a.ingestion_timestamp)]
            assert statuses == ["ACTIVE", "CLEARED"], code
            assert len({a.alarm_id for a in pair}) == 1, code
            cleared = next(a for a in pair if a.status.value == "CLEARED")
            opened = next(a for a in pair if a.status.value == "ACTIVE")
            assert cleared.cleared_timestamp is not None
            assert cleared.cleared_timestamp > opened.event_timestamp

    def test_cell_degradation_phases(self) -> None:
        d = CellDegradation(
            "CELL-0421",
            "SITE-102",
            start=DEGRADATION_START,
            resolved_at=DEGRADATION_START + timedelta(hours=3),
        )
        assert d.weights(DEGRADATION_START - timedelta(minutes=1)) == (0.0, 0.0)
        assert d.weights(DEGRADATION_START + timedelta(hours=1)) == (1.0, 0.0)
        wd, wp = d.weights(DEGRADATION_START + timedelta(hours=3, minutes=16))
        assert wd == 0.0
        assert wp == pytest.approx(1.0, abs=0.02)
        assert d.weights(DEGRADATION_START + timedelta(hours=20)) == (0.0, 0.0)
        for minutes in range(0, 600, 7):
            wd, wp = d.weights(DEGRADATION_START + timedelta(minutes=minutes))
            assert 0 <= wd <= 1
            assert 0 <= wp <= 1
            assert wd + wp <= 1 + 1e-9

    def test_ground_truth_describes_the_scenario(self, ctx: GenContext) -> None:
        truth = next(s for s in ctx.truth.scenarios if s["scenario"] == "cell_degradation")
        assert truth["cell_id"] == "CELL-0421"
        assert truth["router_id"] == "ROUTER-082"


class TestFraudScenario:
    def test_burst_has_the_prd_shape(self, emitted: list[Emitted]) -> None:
        msisdn = f"+2230{FRAUD_INDEX:07d}"
        burst = [
            t
            for t in _of(emitted, "mobilemoney.transactions")
            if t.sender_account == msisdn and t.event_timestamp >= FRAUD_START
        ]
        burst.sort(key=lambda t: t.event_timestamp)
        assert len(burst) == 9
        assert [float(t.amount) for t in burst][-1] == 950_000
        assert burst[-1].transaction_type.value == "CASH_OUT"
        assert burst[-1].event_timestamp - burst[0].event_timestamp <= timedelta(minutes=20)
        assert all(t.status.value == "COMPLETED" for t in burst)
        assert all(t.region == "KDL" for t in burst)

    def test_amounts_escalate(self, emitted: list[Emitted]) -> None:
        msisdn = f"+2230{FRAUD_INDEX:07d}"
        burst = sorted(
            (
                t
                for t in _of(emitted, "mobilemoney.transactions")
                if t.sender_account == msisdn and t.event_timestamp >= FRAUD_START
            ),
            key=lambda t: t.event_timestamp,
        )
        amounts = [float(t.amount) for t in burst]
        assert amounts == sorted(amounts)

    def test_device_differs_from_the_owners(self, emitted: list[Emitted], ctx: GenContext) -> None:
        owner = ctx.population.subscriber(FRAUD_INDEX)
        msisdn = owner.msisdn
        devices = {
            t.device_id
            for t in _of(emitted, "mobilemoney.transactions")
            if t.sender_account == msisdn and t.event_timestamp >= FRAUD_START
        }
        assert owner.imei not in devices

    def test_sim_swap_precedes_the_burst(self, emitted: list[Emitted], ctx: GenContext) -> None:
        msisdn = ctx.population.subscriber(FRAUD_INDEX).msisdn
        swaps = [
            c
            for c in _of(emitted, "customer.events")
            if c.subscriber_id == msisdn and c.event_type.value == "SIM_SWAP"
        ]
        assert any(s.event_timestamp == FRAUD_START - timedelta(minutes=30) for s in swaps)

    def test_ground_truth_lists_every_fraud_transaction(
        self, ctx: GenContext, emitted: list[Emitted]
    ) -> None:
        ids = {e["transaction_id"] for e in ctx.truth.events if e["kind"] == "fraud_transaction"}
        generated = {t.transaction_id for t in _of(emitted, "mobilemoney.transactions")}
        assert len(ids) == 9
        assert ids <= generated


# -- Engine, sinks, command line -----------------------------------------------------


class TestEngine:
    def test_naive_datetimes_are_refused(self, ctx: GenContext) -> None:
        with pytest.raises(ValueError, match="UTC"):
            generate_topic(ctx, "cdr.events", datetime(2026, 9, 1, 12), datetime(2026, 9, 1, 13))  # noqa: DTZ001 - naive on purpose

    def test_empty_window_is_refused(self, ctx: GenContext) -> None:
        with pytest.raises(ValueError, match="after start"):
            generate_topic(ctx, "cdr.events", START, START)

    def test_unknown_topic_is_refused(self, ctx: GenContext) -> None:
        with pytest.raises(KeyError, match="nope"):
            generate(ctx, START, START + timedelta(minutes=1), topics=["nope"])

    def test_topics_can_be_selected(self, ctx: GenContext) -> None:
        only = generate(ctx, START, START + timedelta(minutes=5), topics=["sms.events"])
        assert {e.topic for e in only} == {"sms.events"}


class TestSinks:
    def test_memory_sink_groups_by_topic(self, ctx: GenContext) -> None:
        sink = MemorySink()
        written = sink.write(generate(ctx, START, START + timedelta(minutes=5)))
        assert written == sum(len(v) for v in sink.by_topic.values())
        assert "cdr.events" in sink.by_topic

    def test_jsonl_sink_writes_one_valid_event_per_line(
        self, ctx: GenContext, tmp_path: Path
    ) -> None:
        sink = JsonlSink(tmp_path / "out")
        sink.write(generate(ctx, START, START + timedelta(minutes=5), topics=["ussd.events"]))
        lines = (tmp_path / "out" / "ussd.events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == sink.counts["ussd.events"] > 0
        model = BY_TOPIC["ussd.events"]
        assert all(model.model_validate_json(line) for line in lines)


class TestCli:
    def test_run_writes_files_and_ground_truth(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "events"
        code = cli.main(
            ["--out-dir", str(out), "--duration-minutes", "20", "--start", "2026-09-01T17:00:00Z"]
        )
        assert code == 0
        assert {p.name for p in out.glob("*.jsonl")} == {f"{t}.jsonl" for t in BY_TOPIC}
        truth = json.loads((out / "ground_truth.json").read_text(encoding="utf-8"))
        assert {s["scenario"] for s in truth["scenarios"]} == {"cell_degradation", "fraud_burst"}
        assert "Generated" in capsys.readouterr().out

    def test_two_runs_are_byte_identical_and_do_not_append(self, tmp_path: Path) -> None:
        out = tmp_path / "events"
        args = ["--out-dir", str(out), "--duration-minutes", "10", "--scenario", "none"]
        cli.main(args)
        first = (out / "cdr.events.jsonl").read_bytes()
        cli.main(args)
        assert (out / "cdr.events.jsonl").read_bytes() == first

    def test_scenario_none_has_empty_truth(self, tmp_path: Path) -> None:
        cli.main(["--out-dir", str(tmp_path), "--duration-minutes", "5", "--scenario", "none"])
        assert json.loads((tmp_path / "ground_truth.json").read_text(encoding="utf-8")) == {
            "scenarios": [],
            "events": [],
        }

    @pytest.mark.parametrize(
        "args",
        [["--duration-minutes", "0"], ["--start", "2026-09-01T10:00:00"], ["--speed-factor", "5"]],
    )
    def test_invalid_arguments_exit_with_code_two(
        self, tmp_path: Path, args: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["--out-dir", str(tmp_path), *args]) == 2
        assert "ERROR" in capsys.readouterr().err
