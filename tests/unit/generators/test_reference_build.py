"""Tests for the synthetic reference-data generator and its command line."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from telecom_common.domain import BackhaulType, SiteType, Technology
from telecom_common.reference import CatalogError, ReferenceCatalog
from telecomx_generators.reference import (
    ANCHORS,
    CELLS_BY_TECHNOLOGY,
    SITE_COUNT,
    TENANT_ID,
    TOTAL_SUBSCRIBERS,
    build_catalog,
    cli,
)
from telecomx_generators.reference.build import (
    _apportion,
    _Site,
    _technology_weight,
)
from telecomx_generators.reference.profiles import REFERENCE_DATE, REGION_PROFILES

pytestmark = pytest.mark.unit

#: Invariants must hold for any seed, not just the committed one. The extra seeds are
#: arbitrary: what matters is that they differ.
SEEDS = [42, 1, 7, 2026]


@pytest.fixture(scope="module")
def catalog() -> ReferenceCatalog:
    return build_catalog()


@pytest.fixture(scope="module", params=SEEDS)
def seeded(request: pytest.FixtureRequest) -> ReferenceCatalog:
    return build_catalog(request.param)


class TestTargetsFromThePrd:
    """The figures of PRD section 2.1 are met exactly, whatever the seed."""

    def test_site_count(self, seeded: ReferenceCatalog) -> None:
        assert len(seeded.sites) == SITE_COUNT == 1610

    def test_cell_count_and_mix(self, seeded: ReferenceCatalog) -> None:
        assert len(seeded.cells) == 4821
        by_tech = Counter(c.technology for c in seeded.cells)
        assert dict(by_tech) == dict(CELLS_BY_TECHNOLOGY)

    def test_subscribers_sum_exactly(self, seeded: ReferenceCatalog) -> None:
        assert sum(r.subscriber_count for r in seeded.regions) == TOTAL_SUBSCRIBERS == 12_400_000

    def test_nine_regions(self, seeded: ReferenceCatalog) -> None:
        assert len(seeded.regions) == 9

    def test_three_tenants_but_only_one_network(self, seeded: ReferenceCatalog) -> None:
        assert len(seeded.tenants) == 3
        assert {s.tenant_id for s in seeded.sites} == {TENANT_ID}


class TestStructuralInvariants:
    def test_the_catalogue_is_coherent(self, seeded: ReferenceCatalog) -> None:
        assert seeded.validate() == []

    def test_every_site_hosts_at_least_one_cell(self, seeded: ReferenceCatalog) -> None:
        hosted = {c.site_id for c in seeded.cells}
        assert hosted == {s.site_id for s in seeded.sites}

    def test_no_site_hosts_more_than_three_sectors_of_one_technology(
        self, seeded: ReferenceCatalog
    ) -> None:
        per_site_tech = Counter((c.site_id, c.technology) for c in seeded.cells)
        assert max(per_site_tech.values()) <= 3

    def test_cell_identifiers_are_a_dense_sequence(self, seeded: ReferenceCatalog) -> None:
        numbers = sorted(int(c.cell_id.split("-")[1]) for c in seeded.cells)
        assert numbers == list(range(1, 4822))

    def test_site_identifiers_are_a_dense_sequence(self, seeded: ReferenceCatalog) -> None:
        numbers = sorted(int(s.site_id.split("-")[1]) for s in seeded.sites)
        assert numbers == list(range(1, SITE_COUNT + 1))

    def test_sectors_of_a_site_do_not_share_an_azimuth(self, seeded: ReferenceCatalog) -> None:
        seen: Counter[tuple[str, Technology, int]] = Counter(
            (c.site_id, c.technology, c.azimuth_degrees) for c in seeded.cells
        )
        assert max(seen.values()) == 1

    def test_dates_do_not_exceed_the_reference_date(self, seeded: ReferenceCatalog) -> None:
        assert all(s.commissioned_date <= REFERENCE_DATE for s in seeded.sites)
        assert all(e.last_maintenance_date <= REFERENCE_DATE for e in seeded.equipment)

    def test_coordinates_stay_inside_the_country_box(self, seeded: ReferenceCatalog) -> None:
        for site in seeded.sites:
            assert 10.1 <= site.latitude <= 25.0
            assert -12.3 <= site.longitude <= 4.3

    def test_the_capital_is_the_densest_region(self, seeded: ReferenceCatalog) -> None:
        by_region = Counter(s.region_code for s in seeded.sites)
        assert by_region.most_common(1)[0][0] == "BKO"

    def test_a_cell_never_has_more_users_capacity_than_a_sane_bound(
        self, seeded: ReferenceCatalog
    ) -> None:
        assert all(c.capacity_users <= 2_000 for c in seeded.cells)

    def test_capacity_grows_with_the_technology_generation(self, seeded: ReferenceCatalog) -> None:
        """A sanity check on the capacity model: newer generations carry more."""
        mean = {
            tech: sum(c.capacity_mb_per_hour for c in seeded.cells if c.technology is tech)
            / CELLS_BY_TECHNOLOGY[tech]
            for tech in Technology
        }
        assert mean[Technology.G2] < mean[Technology.G3] < mean[Technology.G4] < mean[Technology.G5]

    def test_a_small_share_of_cells_is_inactive(self, seeded: ReferenceCatalog) -> None:
        share = sum(not c.is_active for c in seeded.cells) / len(seeded.cells)
        assert 0.0 < share < 0.05


class TestRegionalShape:
    """The assumptions of the profiles, observable in the output."""

    def test_the_far_north_leans_on_satellite_and_the_capital_does_not(
        self, catalog: ReferenceCatalog
    ) -> None:
        def satellite_share(region: str) -> float:
            sites = [s for s in catalog.sites if s.region_code == region]
            return sum(s.backhaul_type is BackhaulType.SATELLITE for s in sites) / len(sites)

        assert satellite_share("BKO") == 0.0
        assert satellite_share("KDL") > satellite_share("SKS")
        assert satellite_share("TBT") > 0.15

    def test_the_capital_has_the_most_fibre(self, catalog: ReferenceCatalog) -> None:
        def fibre_share(region: str) -> float:
            sites = [s for s in catalog.sites if s.region_code == region]
            return sum(s.backhaul_type is BackhaulType.FIBER for s in sites) / len(sites)

        assert fibre_share("BKO") > max(
            fibre_share(r.code) for r in REGION_PROFILES if r.code != "BKO"
        )

    def test_5g_is_concentrated_in_the_capital(self, catalog: ReferenceCatalog) -> None:
        site_region = {s.site_id: s.region_code for s in catalog.sites}
        by_region = Counter(
            site_region[c.site_id] for c in catalog.cells if c.technology is Technology.G5
        )
        assert by_region["BKO"] > sum(by_region.values()) / 3

    def test_remote_regions_have_more_backup_power(self, catalog: ReferenceCatalog) -> None:
        def backup(region: str) -> float:
            sites = [s for s in catalog.sites if s.region_code == region]
            return sum(s.has_backup_power for s in sites) / len(sites)

        assert backup("TBT") > backup("BKO")


class TestTechnologyEligibility:
    @staticmethod
    def _site(*, urban: bool, site_type: SiteType) -> _Site:
        from datetime import date

        return _Site(
            number=1,
            profile=REGION_PROFILES[0],
            is_urban=urban,
            site_type=site_type,
            latitude=12.0,
            longitude=-8.0,
            commissioned=date(2015, 1, 1),
            backup_power=False,
            backhaul=BackhaulType.FIBER,
            backhaul_mbps=1000.0,
        )

    def test_5g_is_never_placed_on_a_rural_site(self) -> None:
        assert (
            _technology_weight(self._site(urban=False, site_type=SiteType.MACRO), Technology.G5)
            == 0.0
        )

    def test_5g_is_possible_on_an_urban_site(self) -> None:
        assert (
            _technology_weight(self._site(urban=True, site_type=SiteType.ROOFTOP), Technology.G5)
            > 0
        )

    def test_2g_is_never_placed_indoors(self) -> None:
        assert (
            _technology_weight(self._site(urban=True, site_type=SiteType.INDOOR), Technology.G2)
            == 0.0
        )

    def test_2g_is_favoured_in_rural_areas(self) -> None:
        rural = _technology_weight(self._site(urban=False, site_type=SiteType.MACRO), Technology.G2)
        urban = _technology_weight(self._site(urban=True, site_type=SiteType.MACRO), Technology.G2)
        assert rural > urban


class TestEquipmentLayout:
    def test_every_site_has_exactly_one_of_each_basic_equipment(
        self, seeded: ReferenceCatalog
    ) -> None:
        per_site: dict[str, Counter[str]] = {}
        for item in seeded.equipment:
            per_site.setdefault(item.site_id, Counter())[item.equipment_type] += 1
        for site in seeded.sites:
            counts = per_site[site.site_id]
            assert counts["BBU"] == counts["ROUTER"] == counts["POWER_SUPPLY"] == 1, site.site_id

    def test_batteries_follow_backup_power(self, seeded: ReferenceCatalog) -> None:
        with_battery = {e.site_id for e in seeded.equipment if e.equipment_type == "BATTERY"}
        backed = {s.site_id for s in seeded.sites if s.has_backup_power}
        assert with_battery == backed

    def test_transport_equipment_follows_the_backhaul_type(self, seeded: ReferenceCatalog) -> None:
        microwave = {e.site_id for e in seeded.equipment if e.equipment_type == "MICROWAVE_LINK"}
        fibre = {e.site_id for e in seeded.equipment if e.equipment_type == "FIBER_TERMINAL"}
        for site in seeded.sites:
            assert (site.site_id in microwave) == (site.backhaul_type is BackhaulType.MICROWAVE)
            assert (site.site_id in fibre) == (site.backhaul_type is BackhaulType.FIBER)

    def test_the_fleet_size_matches_the_telemetry_volume_of_the_prd(
        self, catalog: ReferenceCatalog
    ) -> None:
        """PRD section 2.2 gives device.events about 40 msg/s. With the fleet size, that
        means each piece of equipment reports every few minutes, which is plausible."""
        interval_seconds = len(catalog.equipment) / 40
        assert 120 <= interval_seconds <= 600

    def test_some_equipment_is_past_its_expected_lifetime(self, catalog: ReferenceCatalog) -> None:
        """The population predictive maintenance exists for. A fleet with no ageing
        equipment would give the model nothing to learn."""
        aged = [
            e
            for e in catalog.equipment
            if (REFERENCE_DATE - e.installed_date).days / 365.25 > e.expected_lifetime_years
        ]
        assert aged


class TestScenarioAnchors:
    """The objects the demonstration scenario names, with the properties that make it work."""

    def test_the_degraded_cell_sits_on_the_degraded_site(self, seeded: ReferenceCatalog) -> None:
        cell = seeded.get("cells", "CELL-0421", TENANT_ID)
        assert cell.site_id == "SITE-102"
        assert cell.technology is Technology.G4

    def test_the_degraded_site_is_in_the_capital(self, seeded: ReferenceCatalog) -> None:
        assert seeded.get("sites", "SITE-102", TENANT_ID).region_code == "BKO"

    def test_the_degraded_site_has_several_4g_cells(self, seeded: ReferenceCatalog) -> None:
        cells = [
            c for c in seeded.cells if c.site_id == "SITE-102" and c.technology is Technology.G4
        ]
        assert len(cells) >= ANCHORS.min_4g_cells_on_degraded_site

    def test_the_router_belongs_to_the_degraded_site(self, seeded: ReferenceCatalog) -> None:
        router = seeded.get("equipment", "ROUTER-082", TENANT_ID)
        assert router.site_id == "SITE-102"
        assert router.equipment_type == "ROUTER"

    def test_the_saturating_cell_is_a_4g_cell_in_the_capital(
        self, seeded: ReferenceCatalog
    ) -> None:
        cell = seeded.get("cells", "CELL-0721", TENANT_ID)
        assert cell.technology is Technology.G4
        assert seeded.get("sites", cell.site_id, TENANT_ID).region_code == "BKO"

    def test_both_anchor_cells_carry_the_capacity_of_the_prd_examples(
        self, seeded: ReferenceCatalog
    ) -> None:
        for number in ("CELL-0421", "CELL-0721"):
            cell = seeded.get("cells", number, TENANT_ID)
            assert cell.capacity_mb_per_hour == 102_400  # 100 GB/h
            assert cell.is_active

    def test_the_backhaul_can_genuinely_congest(self, seeded: ReferenceCatalog) -> None:
        """The relation the scenario rests on: the link is smaller than what the cells
        can demand at full load. Conversion: 1 MB/hour = 8 / 3600 Mbps."""
        site = seeded.get("sites", "SITE-102", TENANT_ID)
        demand_mbps = (
            sum(c.capacity_mb_per_hour for c in seeded.cells if c.site_id == "SITE-102") * 8 / 3600
        )
        assert site.backhaul_type is BackhaulType.MICROWAVE
        assert site.backhaul_capacity_mbps < demand_mbps

    def test_the_prd_example_load_fits_the_anchor_cell(self, seeded: ReferenceCatalog) -> None:
        cell = seeded.get("cells", "CELL-0421", TENANT_ID)
        assert cell.capacity_users >= 842  # active_users of the PRD example
        assert cell.capacity_mb_per_hour >= 48_291  # traffic_mb of the PRD example

    def test_the_identifiers_are_not_taken_by_other_objects(self, seeded: ReferenceCatalog) -> None:
        """Swapping the anchors in must not have duplicated or lost an identifier."""
        assert len({c.cell_id for c in seeded.cells}) == len(seeded.cells)
        assert len({e.equipment_id for e in seeded.equipment}) == len(seeded.equipment)


class TestDeterminism:
    def test_the_same_seed_gives_byte_identical_files(self) -> None:
        assert build_catalog(42).render_csv() == build_catalog(42).render_csv()

    def test_another_seed_gives_another_network(self) -> None:
        assert (
            build_catalog(42).render_csv()["sites.csv"]
            != build_catalog(43).render_csv()["sites.csv"]
        )

    def test_anchors_do_not_depend_on_the_seed(self) -> None:
        """The scenario must work whichever seed is used."""
        for seed in (3, 11):
            built = build_catalog(seed)
            assert built.get("cells", "CELL-0421", TENANT_ID).site_id == "SITE-102"
            assert built.get("equipment", "ROUTER-082", TENANT_ID).site_id == "SITE-102"

    def test_parts_of_the_build_use_independent_streams(self) -> None:
        """Changing how cells are built must not reshuffle the sites: they are drawn
        from separate random streams."""
        first = build_catalog(5).render_csv()
        second = build_catalog(5).render_csv()
        assert first["sites.csv"] == second["sites.csv"]
        assert first["cells.csv"] == second["cells.csv"]

    def test_the_reference_date_is_fixed_not_the_clock(self) -> None:
        """An age computed from today's date would change the data every day."""
        assert REFERENCE_DATE.isoformat() == "2026-09-01"


class TestGeneratorGuards:
    """The generator refuses to emit data it knows to be wrong."""

    def test_an_infeasible_cell_mix_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """More 5G cells than eligible urban sites can host: fail loudly, not silently
        place them somewhere implausible."""
        from telecomx_generators.reference import build

        monkeypatch.setattr(build, "CELLS_BY_TECHNOLOGY", {Technology.G5: 1_000_000})
        with pytest.raises(CatalogError, match="not enough eligible sites"):
            build.build_catalog()

    def test_an_incoherent_catalogue_never_reaches_a_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ReferenceCatalog, "validate", lambda _self: ["something is wrong"])
        with pytest.raises(CatalogError, match="incoherent catalogue"):
            build_catalog()


class TestApportion:
    def test_sums_exactly_to_the_total(self) -> None:
        assert sum(_apportion(1610, [0.199, 0.12, 0.31, 0.371])) == 1610

    def test_is_proportional(self) -> None:
        assert _apportion(100, [1, 1, 2]) == [25, 25, 50]

    def test_hands_out_the_remainder_to_the_largest_fractions(self) -> None:
        assert _apportion(10, [1, 1, 1]) == [4, 3, 3]
        assert sum(_apportion(10, [1, 1, 1])) == 10

    @pytest.mark.parametrize("total", [0, 1, 7, 1610, 12_400_000])
    def test_conserves_any_total(self, total: int) -> None:
        shares = _apportion(total, [0.28, 0.14, 0.13, 0.12, 0.09, 0.10, 0.05, 0.06, 0.03])
        assert sum(shares) == total
        assert all(s >= 0 for s in shares)


class TestCommandLine:
    def test_the_committed_seeds_are_up_to_date(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The gate: a generator change without regenerating cannot be merged."""
        assert cli.main(["--check"]) == 0
        assert "up to date" in capsys.readouterr().out

    def test_generate_then_check(self, tmp_path: Path) -> None:
        assert cli.main(["--out-dir", str(tmp_path)]) == 0
        assert cli.main(["--out-dir", str(tmp_path), "--check"]) == 0
        assert len(list(tmp_path.glob("*.csv"))) == 8

    def test_check_fails_when_nothing_was_generated(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["--out-dir", str(tmp_path), "--check"]) == 1
        assert "missing" in capsys.readouterr().err

    def test_check_detects_a_hand_edited_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.main(["--out-dir", str(tmp_path)])
        target = tmp_path / "cells.csv"
        target.write_text(target.read_text(encoding="utf-8") + "CELL-9999\n", encoding="utf-8")
        assert cli.main(["--out-dir", str(tmp_path), "--check"]) == 1
        stderr = capsys.readouterr().err
        assert "outdated" in stderr
        assert "cells.csv" in stderr

    def test_a_different_seed_is_detected_as_outdated(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["--check", "--seed", "43"]) == 1
        assert "outdated" in capsys.readouterr().err

    def test_writing_is_idempotent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.main(["--out-dir", str(tmp_path)])
        capsys.readouterr()
        cli.main(["--out-dir", str(tmp_path)])
        assert "0 written" in capsys.readouterr().out

    def test_a_failing_generator_exits_with_code_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom(_seed: int) -> ReferenceCatalog:
            msg = "incoherent on purpose"
            raise CatalogError(msg)

        monkeypatch.setattr(cli, "build_catalog", boom)
        assert cli.main(["--out-dir", str(tmp_path)]) == 2
        assert "incoherent on purpose" in capsys.readouterr().err

    def test_seed_files_use_unix_line_endings(self) -> None:
        for path in cli.DEFAULT_OUT_DIR.glob("*.csv"):
            assert b"\r\n" not in path.read_bytes(), path.name
