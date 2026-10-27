"""Tests for the reference catalogue: persistence, coherence and contract rules."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from contract_samples import valid_sample

from telecom_common.contracts import load_manifest, parse_avsc
from telecom_common.domain import (
    AlarmTypeRef,
    BackhaulType,
    BundleRef,
    CellRef,
    EquipmentRef,
    RegionRef,
    SiteRef,
    SiteType,
    Technology,
    TechnologyRef,
    TenantRef,
)
from telecom_common.reference import TABLES, CatalogError, ReferenceCatalog

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
SEEDS = REPO_ROOT / "data-platform" / "dbt" / "seeds"
TS = {"type": "long", "logicalType": "timestamp-micros"}
ML = "telecomx-ml"
SN = "telecomx-sn"


def _catalog() -> ReferenceCatalog:
    """The smallest coherent catalogue: one of everything, for two tenants' worth of ids."""
    return ReferenceCatalog(
        tenants=(
            TenantRef(tenant_id=ML, name="TelecomX Mali", country_code="ML", currency="XOF"),
            TenantRef(tenant_id=SN, name="TelecomX Sénégal", country_code="SN", currency="XOF"),
        ),
        technologies=(
            TechnologyRef(
                technology=Technology.G4, label="4G", downlink_mbps=300, reference_latency_ms=40
            ),
        ),
        regions=(
            RegionRef(
                tenant_id=ML,
                region_code="BKO",
                region_name="Bamako",
                population=10,
                area_type="urban",
                subscriber_count=10,
            ),
        ),
        sites=(
            SiteRef(
                tenant_id=ML,
                site_id="SITE-001",
                region_code="BKO",
                latitude=12.6,
                longitude=-8.0,
                site_type=SiteType.MACRO,
                commissioned_date=date(2015, 1, 1),
                has_backup_power=True,
                backhaul_type=BackhaulType.MICROWAVE,
                backhaul_capacity_mbps=311.0,
            ),
        ),
        cells=(
            CellRef(
                tenant_id=ML,
                cell_id="CELL-0001",
                site_id="SITE-001",
                technology=Technology.G4,
                frequency_band="B3-1800",
                azimuth_degrees=0,
                capacity_mb_per_hour=100_000,
                capacity_users=1_000,
                is_active=True,
            ),
        ),
        equipment=(
            EquipmentRef(
                tenant_id=ML,
                equipment_id="BBU-001",
                site_id="SITE-001",
                equipment_type="BBU",
                vendor="Nordwave",
                model="BB-5200",
                installed_date=date(2016, 1, 1),
                expected_lifetime_years=10,
                last_maintenance_date=date(2020, 1, 1),
            ),
        ),
        bundles=(
            BundleRef(
                bundle_code="DATA-1GB",
                label="1 Go",
                bundle_type="DATA",
                allowance_mb=1024.0,
                price_xof=Decimal(1000),
                validity_days=30,
            ),
        ),
        alarm_types=(
            AlarmTypeRef(
                alarm_code="CELL_DOWN",
                label="Cellule HS",
                domain="RADIO",
                default_severity="CRITICAL",
                sop_ref="SOP-NET-001",
            ),
        ),
    )


class TestPersistence:
    def test_csv_round_trip_is_lossless(self) -> None:
        original = _catalog()
        assert ReferenceCatalog.from_csv(original.render_csv()) == original

    def test_one_file_per_table(self) -> None:
        assert set(_catalog().render_csv()) == {f"{t.name}.csv" for t in TABLES}

    def test_csv_has_a_header_and_unix_line_endings(self) -> None:
        text = _catalog().render_csv()["cells.csv"]
        assert text.splitlines()[0] == ",".join(CellRef.model_fields)
        assert "\r" not in text

    def test_rendering_is_stable(self) -> None:
        """Byte-identical output is what lets CI compare with the committed seeds."""
        assert _catalog().render_csv() == _catalog().render_csv()

    def test_booleans_and_nulls_are_written_in_a_canonical_form(self) -> None:
        files = _catalog().render_csv()
        assert ",true" in files["cells.csv"]
        # DATA-1GB has no minutes: an empty cell, not the string "None".
        assert "None" not in files["bundles.csv"]

    def test_load_reads_a_directory(self, tmp_path: Path) -> None:
        for name, content in _catalog().render_csv().items():
            (tmp_path / name).write_text(content, encoding="utf-8", newline="\n")
        assert ReferenceCatalog.load(tmp_path) == _catalog()

    def test_a_missing_file_is_reported(self) -> None:
        files = _catalog().render_csv()
        del files["cells.csv"]
        with pytest.raises(CatalogError, match=r"missing reference file cells\.csv"):
            ReferenceCatalog.from_csv(files)

    def test_a_column_mismatch_is_reported(self) -> None:
        files = _catalog().render_csv()
        files["cells.csv"] = files["cells.csv"].replace("capacity_users", "capacity_user", 1)
        with pytest.raises(CatalogError, match="do not match expected"):
            ReferenceCatalog.from_csv(files)

    def test_an_invalid_row_names_its_file_and_line(self) -> None:
        files = _catalog().render_csv()
        files["cells.csv"] = files["cells.csv"].replace("CELL-0001", "CELL-1", 1)
        with pytest.raises(CatalogError, match=r"cells\.csv line 2"):
            ReferenceCatalog.from_csv(files)

    def test_an_incoherent_catalogue_is_refused_at_load(self) -> None:
        files = _catalog().render_csv()
        files["cells.csv"] = files["cells.csv"].replace("SITE-001", "SITE-999", 1)
        with pytest.raises(CatalogError, match="incoherent"):
            ReferenceCatalog.from_csv(files)


class TestCoherence:
    def test_the_minimal_catalogue_is_sound(self) -> None:
        assert _catalog().validate() == []

    def test_duplicate_key(self) -> None:
        catalog = _catalog()
        broken = replace(catalog, cells=catalog.cells * 2)
        assert any("duplicate key 'CELL-0001'" in p for p in broken.validate())

    def test_the_same_id_for_two_tenants_is_not_a_duplicate(self) -> None:
        """Identifiers are unique per tenant, not globally (ADR-016)."""
        catalog = _catalog()
        other = catalog.cells[0].model_copy(update={"tenant_id": SN})
        problems = replace(catalog, cells=(*catalog.cells, other)).validate()
        assert not any("duplicate" in p for p in problems)

    def test_cell_on_an_unknown_site(self) -> None:
        catalog = _catalog()
        orphan = catalog.cells[0].model_copy(update={"cell_id": "CELL-0002", "site_id": "SITE-999"})
        problems = replace(catalog, cells=(*catalog.cells, orphan)).validate()
        assert any("CELL-0002 references unknown site" in p for p in problems)

    def test_a_site_of_another_tenant_is_not_a_valid_parent(self) -> None:
        catalog = _catalog()
        wrong_tenant = catalog.cells[0].model_copy(update={"tenant_id": SN})
        assert any("unknown site" in p for p in replace(catalog, cells=(wrong_tenant,)).validate())

    def test_site_without_cell(self) -> None:
        assert any("hosts no cell" in p for p in replace(_catalog(), cells=()).validate())

    def test_site_in_an_unknown_region(self) -> None:
        catalog = _catalog()
        lost = catalog.sites[0].model_copy(update={"region_code": "XXX"})
        assert any("unknown region" in p for p in replace(catalog, sites=(lost,)).validate())

    def test_region_of_an_unknown_tenant(self) -> None:
        catalog = _catalog()
        lost = catalog.regions[0].model_copy(update={"tenant_id": "telecomx-zz"})
        assert any("unknown tenant" in p for p in replace(catalog, regions=(lost,)).validate())

    def test_cell_with_an_unknown_technology(self) -> None:
        catalog = _catalog()
        no_techs = replace(catalog, technologies=())
        assert any("unknown technology" in p for p in no_techs.validate())

    def test_equipment_installed_before_its_site_existed(self) -> None:
        catalog = _catalog()
        early = catalog.equipment[0].model_copy(update={"installed_date": date(2010, 1, 1)})
        assert any(
            "before its site was commissioned" in p
            for p in replace(catalog, equipment=(early,)).validate()
        )

    def test_equipment_maintained_before_being_installed(self) -> None:
        catalog = _catalog()
        odd = catalog.equipment[0].model_copy(update={"last_maintenance_date": date(2015, 6, 1)})
        assert any(
            "maintained before being installed" in p
            for p in replace(catalog, equipment=(odd,)).validate()
        )

    def test_equipment_on_an_unknown_site(self) -> None:
        catalog = _catalog()
        lost = catalog.equipment[0].model_copy(update={"site_id": "SITE-999"})
        assert any("unknown site" in p for p in replace(catalog, equipment=(lost,)).validate())

    @pytest.mark.parametrize(
        ("table", "field", "bad", "message"),
        [
            ("equipment", "equipment_type", "TOASTER", "unknown type"),
            ("bundles", "bundle_type", "PIGEON", "unknown type"),
            ("alarm_types", "domain", "WEATHER", "unknown domain"),
            ("alarm_types", "default_severity", "PANIC", "unknown severity"),
        ],
    )
    def test_enumerations_are_checked_against_the_contracts(
        self, table: str, field: str, bad: str, message: str
    ) -> None:
        """The legal values live in the generated contract enums, not here."""
        catalog = _catalog()
        row = getattr(catalog, table)[0].model_copy(update={field: bad})
        assert any(message in p for p in replace(catalog, **{table: (row,)}).validate())

    def test_every_problem_is_reported_not_just_the_first(self) -> None:
        catalog = _catalog()
        bad_cell = catalog.cells[0].model_copy(update={"site_id": "SITE-999"})
        bad_item = catalog.equipment[0].model_copy(update={"equipment_type": "TOASTER"})
        problems = replace(catalog, cells=(bad_cell,), equipment=(bad_item,)).validate()
        assert len(problems) >= 3


class TestLookup:
    def test_get_finds_a_row(self) -> None:
        assert _catalog().get("cells", "CELL-0001", ML).site_id == "SITE-001"

    def test_get_returns_none_for_an_absent_key(self) -> None:
        assert _catalog().get("cells", "CELL-0002", ML) is None

    def test_a_cell_of_one_tenant_does_not_exist_for_another(self) -> None:
        """Isolation (ADR-016): same identifier, different tenant, no row."""
        assert _catalog().get("cells", "CELL-0001", SN) is None

    def test_a_scoped_table_requires_a_tenant(self) -> None:
        """Answering 'not found' would hide a caller bug."""
        with pytest.raises(ValueError, match="tenant-scoped"):
            _catalog().get("cells", "CELL-0001")

    def test_a_global_table_ignores_the_tenant(self) -> None:
        catalog = _catalog()
        assert catalog.has("bundles", "DATA-1GB")
        assert catalog.has("bundles", "DATA-1GB", ML)

    def test_unknown_table(self) -> None:
        with pytest.raises(KeyError):
            _catalog().get("ghosts", "x", ML)

    def test_has(self) -> None:
        assert _catalog().has("sites", "SITE-001", ML)
        assert not _catalog().has("sites", "SITE-001", SN)

    def test_keys_are_scoped_to_the_tenant(self) -> None:
        assert _catalog().keys("cells", ML) == {"CELL-0001"}
        assert _catalog().keys("cells", SN) == frozenset()

    def test_keys_of_a_scoped_table_require_a_tenant(self) -> None:
        with pytest.raises(ValueError, match="tenant-scoped"):
            _catalog().keys("cells")

    def test_keys_of_a_global_table(self) -> None:
        assert _catalog().keys("tenants") == {ML, SN}


class TestCheckRecord:
    """The contract rules a single record cannot check: in_ref and lte_ref."""

    SPEC = load_manifest()["network-metrics-v1"]

    def _metrics(self, **overrides: Any) -> dict[str, Any]:
        record = valid_sample(self.SPEC)
        record.update(
            tenant_id=ML, cell_id="CELL-0001", site_id="SITE-001", region="BKO", active_users=10
        )
        record.update(overrides)
        return record

    def test_a_consistent_event_has_no_violation(self) -> None:
        assert _catalog().check_record(self.SPEC, self._metrics()) == []

    def test_unknown_cell(self) -> None:
        problems = _catalog().check_record(self.SPEC, self._metrics(cell_id="CELL-0999"))
        assert any("cell_id='CELL-0999' does not exist in cells" in p for p in problems)

    def test_cell_of_another_tenant(self) -> None:
        problems = _catalog().check_record(self.SPEC, self._metrics(tenant_id=SN))
        assert any("does not exist in cells" in p for p in problems)

    def test_unknown_tenant(self) -> None:
        problems = _catalog().check_record(self.SPEC, self._metrics(tenant_id="telecomx-zz"))
        assert any("does not exist in tenants" in p for p in problems)

    def test_unknown_site_and_region_are_both_reported(self) -> None:
        problems = _catalog().check_record(
            self.SPEC, self._metrics(site_id="SITE-999", region="XXX")
        )
        assert any("site_id" in p for p in problems)
        assert any("region" in p for p in problems)

    def test_active_users_within_capacity(self) -> None:
        assert _catalog().check_record(self.SPEC, self._metrics(active_users=1_000)) == []

    def test_active_users_above_cell_capacity(self) -> None:
        """The rule of PRD section 15: active_users <= cell_capacity."""
        problems = _catalog().check_record(self.SPEC, self._metrics(active_users=1_001))
        assert any("active_users=1001 exceeds cells.capacity_users=1000" in p for p in problems)

    def test_missing_tenant_cannot_resolve_scoped_references(self) -> None:
        record = self._metrics()
        del record["tenant_id"]
        problems = _catalog().check_record(self.SPEC, record)
        assert any("without a tenant_id" in p for p in problems)

    def test_an_absent_optional_reference_is_skipped(self) -> None:
        spec = parse_avsc(
            "probe-v1",
            {
                "type": "record",
                "name": "Probe",
                "namespace": "t",
                "doc": "d",
                "telecomx.owner": "t",
                "telecomx.topic": "p.e",
                "telecomx.partition_key": "event_id",
                "telecomx.sla_freshness_seconds": 1,
                "telecomx.compatibility": "BACKWARD",
                "telecomx.classification": "P4",
                "fields": [
                    {"name": "event_id", "type": "string"},
                    {"name": "event_timestamp", "type": TS},
                    {"name": "ingestion_timestamp", "type": TS},
                    {"name": "schema_version", "type": "string", "default": "1"},
                    {"name": "producer_id", "type": "string"},
                    {"name": "tenant_id", "type": "string"},
                    {
                        "name": "site",
                        "type": ["null", "string"],
                        "default": None,
                        "doc": "d",
                        "telecomx.constraint": "in_ref:sites",
                    },
                ],
            },
        )
        assert _catalog().check_record(spec, {"tenant_id": ML, "site": None}) == []

    @staticmethod
    def _probe(constraint_fields: list[dict[str, Any]]) -> Any:
        return parse_avsc(
            "probe-v1",
            {
                "type": "record",
                "name": "Probe",
                "namespace": "t",
                "doc": "d",
                "telecomx.owner": "t",
                "telecomx.topic": "p.e",
                "telecomx.partition_key": "event_id",
                "telecomx.sla_freshness_seconds": 1,
                "telecomx.compatibility": "BACKWARD",
                "telecomx.classification": "P4",
                "fields": [
                    {"name": "event_id", "type": "string"},
                    {"name": "event_timestamp", "type": TS},
                    {"name": "ingestion_timestamp", "type": TS},
                    {"name": "schema_version", "type": "string", "default": "1"},
                    {"name": "producer_id", "type": "string"},
                    {"name": "tenant_id", "type": "string"},
                    *constraint_fields,
                ],
            },
        )

    def test_a_reference_to_an_unknown_table_is_reported(self) -> None:
        spec = self._probe(
            [{"name": "x", "type": "string", "doc": "d", "telecomx.constraint": "in_ref:ghosts"}]
        )
        problems = _catalog().check_record(spec, {"tenant_id": ML, "x": "a"})
        assert any("unknown table 'ghosts'" in p for p in problems)

    def test_lte_ref_without_a_designating_field_is_ambiguous_not_guessed(self) -> None:
        spec = self._probe(
            [
                {
                    "name": "n",
                    "type": "int",
                    "doc": "d",
                    "telecomx.constraint": "lte_ref:cells.capacity_users",
                }
            ]
        )
        problems = _catalog().check_record(spec, {"tenant_id": ML, "n": 5})
        assert any("ambiguous" in p for p in problems)

    def test_lte_ref_with_two_designating_fields_is_ambiguous(self) -> None:
        spec = self._probe(
            [
                {"name": "a", "type": "string", "doc": "d", "telecomx.constraint": "in_ref:cells"},
                {"name": "b", "type": "string", "doc": "d", "telecomx.constraint": "in_ref:cells"},
                {
                    "name": "n",
                    "type": "int",
                    "doc": "d",
                    "telecomx.constraint": "lte_ref:cells.capacity_users",
                },
            ]
        )
        record = {"tenant_id": ML, "a": "CELL-0001", "b": "CELL-0001", "n": 5}
        assert any("ambiguous" in p for p in _catalog().check_record(spec, record))

    def test_equipment_telemetry_references_equipment(self) -> None:
        spec = load_manifest()["equipment-telemetry-v1"]
        record = {
            **valid_sample(spec),
            "tenant_id": ML,
            "site_id": "SITE-001",
            "equipment_id": "BBU-001",
        }
        assert _catalog().check_record(spec, record) == []
        problems = _catalog().check_record(spec, {**record, "equipment_id": "BBU-009"})
        assert any("does not exist in equipment" in p for p in problems)


@pytest.fixture(scope="module")
def seeds() -> ReferenceCatalog:
    return ReferenceCatalog.load(SEEDS)


class TestAgainstTheCommittedSeeds:
    """The generated reference data and the contracts must agree with each other."""

    def test_the_committed_seeds_load_and_are_coherent(self, seeds: ReferenceCatalog) -> None:
        assert seeds.validate() == []
        assert len(seeds.cells) > 4000

    def test_a_real_cell_makes_a_valid_event(self, seeds: ReferenceCatalog) -> None:
        spec = load_manifest()["network-metrics-v1"]
        cell = seeds.get("cells", "CELL-0421", ML)
        site = seeds.get("sites", cell.site_id, ML)
        record = {
            **valid_sample(spec),
            "tenant_id": ML,
            "cell_id": cell.cell_id,
            "site_id": site.site_id,
            "region": site.region_code,
            "active_users": 842,  # the figure of the PRD example
        }
        assert seeds.check_record(spec, record) == []

    def test_the_prd_example_load_fits_the_anchor_cell(self, seeds: ReferenceCatalog) -> None:
        """842 users and 48 291 MB at 18:00 (PRD section 4) must be possible on CELL-0421."""
        cell = seeds.get("cells", "CELL-0421", ML)
        assert cell.capacity_users >= 842
        assert cell.capacity_mb_per_hour >= 48_291

    @pytest.mark.parametrize(
        "contract_id",
        [
            "network-metrics-v1",
            "network-alarms-v1",
            "cdr-v1",
            "data-session-v1",
            "ussd-v1",
            "equipment-telemetry-v1",
        ],
    )
    def test_the_references_declared_by_the_contracts_resolve(
        self, seeds: ReferenceCatalog, contract_id: str
    ) -> None:
        """Every table named by an in_ref exists, so the rule is applicable."""
        spec = load_manifest()[contract_id]
        from telecom_common.contracts import ConstraintKind

        tables = {
            str(c.value)
            for f in spec.fields
            for c in f.constraints
            if c.kind is ConstraintKind.IN_REF
        }
        assert tables, contract_id
        assert tables <= {t.name for t in TABLES}
