"""Tests for the reference-data entities."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from telecom_common.domain import (
    AlarmTypeRef,
    AreaType,
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

pytestmark = pytest.mark.unit


def _cell(**overrides: Any) -> CellRef:
    data: dict[str, Any] = {
        "tenant_id": "telecomx-ml",
        "cell_id": "CELL-0421",
        "site_id": "SITE-102",
        "technology": "G4",
        "frequency_band": "B3-1800",
        "azimuth_degrees": 120,
        "capacity_mb_per_hour": 102_400,
        "capacity_users": 1_200,
        "is_active": True,
    }
    data.update(overrides)
    return CellRef.model_validate(data)


def _site(**overrides: Any) -> SiteRef:
    data: dict[str, Any] = {
        "tenant_id": "telecomx-ml",
        "site_id": "SITE-102",
        "region_code": "BKO",
        "latitude": 12.64,
        "longitude": -8.0,
        "site_type": "macro",
        "commissioned_date": date(2015, 3, 1),
        "has_backup_power": True,
        "backhaul_type": "microwave",
        "backhaul_capacity_mbps": 311.0,
    }
    data.update(overrides)
    return SiteRef.model_validate(data)


class TestCell:
    def test_accepts_a_valid_cell(self) -> None:
        cell = _cell()
        assert cell.technology is Technology.G4
        assert cell.capacity_mb_per_hour == 102_400

    @pytest.mark.parametrize("bad_id", ["CELL-421", "cell-0421", "SITE-0421", ""])
    def test_rejects_a_malformed_cell_id(self, bad_id: str) -> None:
        with pytest.raises(ValidationError):
            _cell(cell_id=bad_id)

    @pytest.mark.parametrize("field", ["capacity_mb_per_hour", "capacity_users"])
    @pytest.mark.parametrize("value", [0, -1])
    def test_capacity_must_be_positive(self, field: str, value: int) -> None:
        """A zero capacity would make every utilisation ratio divide by zero."""
        with pytest.raises(ValidationError):
            _cell(**{field: value})

    @pytest.mark.parametrize("azimuth", [-1, 360])
    def test_azimuth_must_be_a_compass_bearing(self, azimuth: int) -> None:
        with pytest.raises(ValidationError):
            _cell(azimuth_degrees=azimuth)

    def test_unknown_technology_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _cell(technology="G6")

    def test_is_immutable(self) -> None:
        with pytest.raises(ValidationError, match="frozen"):
            _cell().capacity_users = 5  # type: ignore[misc]

    def test_rejects_an_unknown_column(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            _cell(colour="red")


class TestSite:
    def test_accepts_a_valid_site(self) -> None:
        site = _site()
        assert site.site_type is SiteType.MACRO
        assert site.backhaul_type is BackhaulType.MICROWAVE

    @pytest.mark.parametrize(
        ("field", "value"), [("latitude", 91), ("latitude", -91), ("longitude", 181)]
    )
    def test_coordinates_are_bounded(self, field: str, value: float) -> None:
        with pytest.raises(ValidationError):
            _site(**{field: value})

    def test_backhaul_capacity_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _site(backhaul_capacity_mbps=0)

    @pytest.mark.parametrize("code", ["bko", "BK", "BKOO", "B1O"])
    def test_region_code_is_three_capital_letters(self, code: str) -> None:
        with pytest.raises(ValidationError):
            _site(region_code=code)

    def test_unknown_site_type_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _site(site_type="skyscraper")


class TestTenant:
    def test_accepts_a_valid_tenant(self) -> None:
        TenantRef(tenant_id="telecomx-ml", name="TelecomX Mali", country_code="ML", currency="XOF")

    @pytest.mark.parametrize("tenant_id", ["Telecomx", "ml", "-x", "has space", "UPPER"])
    def test_rejects_a_malformed_tenant_id(self, tenant_id: str) -> None:
        with pytest.raises(ValidationError):
            TenantRef(tenant_id=tenant_id, name="n", country_code="ML", currency="XOF")

    def test_country_and_currency_follow_iso_shapes(self) -> None:
        with pytest.raises(ValidationError):
            TenantRef(tenant_id="telecomx-ml", name="n", country_code="MLI", currency="XOF")
        with pytest.raises(ValidationError):
            TenantRef(tenant_id="telecomx-ml", name="n", country_code="ML", currency="xof")


class TestRegionAndTechnology:
    def test_region(self) -> None:
        region = RegionRef(
            tenant_id="telecomx-ml",
            region_code="BKO",
            region_name="Bamako",
            population=2_860_000,
            area_type="urban",
            subscriber_count=3_472_000,
        )
        assert region.area_type is AreaType.URBAN

    def test_region_counts_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            RegionRef(
                tenant_id="telecomx-ml",
                region_code="BKO",
                region_name="Bamako",
                population=0,
                area_type="urban",
                subscriber_count=1,
            )

    def test_technology_figures_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            TechnologyRef(technology="G4", label="4G", downlink_mbps=0, reference_latency_ms=40)


class TestEquipment:
    def _item(self, **overrides: Any) -> EquipmentRef:
        data: dict[str, Any] = {
            "tenant_id": "telecomx-ml",
            "equipment_id": "ROUTER-082",
            "site_id": "SITE-102",
            "equipment_type": "ROUTER",
            "vendor": "Helix Telecom",
            "model": "RT-A200",
            "installed_date": date(2023, 10, 4),
            "expected_lifetime_years": 8,
            "last_maintenance_date": date(2025, 1, 1),
        }
        data.update(overrides)
        return EquipmentRef.model_validate(data)

    def test_accepts_a_valid_item(self) -> None:
        assert self._item().equipment_id == "ROUTER-082"

    @pytest.mark.parametrize("bad_id", ["router-082", "ROUTER-8", "ROUTER082", "R-082"])
    def test_rejects_a_malformed_equipment_id(self, bad_id: str) -> None:
        with pytest.raises(ValidationError):
            self._item(equipment_id=bad_id)

    def test_lifetime_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            self._item(expected_lifetime_years=0)


class TestBundleAndAlarmType:
    def test_bundle_price_is_an_exact_decimal(self) -> None:
        bundle = BundleRef(
            bundle_code="DATA-10GB",
            label="10 Go",
            bundle_type="DATA",
            allowance_mb=10240.0,
            price_xof=Decimal("5000"),
            validity_days=30,
        )
        assert bundle.price_xof == Decimal(5000)
        assert bundle.allowance_minutes is None

    def test_bundle_rejects_fractional_centimes_beyond_two_places(self) -> None:
        with pytest.raises(ValidationError, match="decimal places"):
            BundleRef(
                bundle_code="X-1",
                label="x",
                bundle_type="DATA",
                price_xof=Decimal("1.005"),
                validity_days=1,
            )

    def test_bundle_validity_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            BundleRef(
                bundle_code="X-1",
                label="x",
                bundle_type="DATA",
                price_xof=Decimal(1),
                validity_days=0,
            )

    def test_alarm_type(self) -> None:
        alarm = AlarmTypeRef(
            alarm_code="BACKHAUL_CONGESTION",
            label="Congestion",
            domain="TRANSPORT",
            default_severity="MAJOR",
            sop_ref="SOP-NET-014",
        )
        assert alarm.sop_ref == "SOP-NET-014"

    def test_sop_reference_is_optional(self) -> None:
        assert (
            AlarmTypeRef(
                alarm_code="X_ALARM", label="x", domain="CORE", default_severity="INFO"
            ).sop_ref
            is None
        )

    @pytest.mark.parametrize("sop", ["SOP-NET-14", "sop-net-014", "SOP-014", "NET-014"])
    def test_sop_reference_must_follow_the_convention(self, sop: str) -> None:
        with pytest.raises(ValidationError):
            AlarmTypeRef(
                alarm_code="X_ALARM", label="x", domain="CORE", default_severity="INFO", sop_ref=sop
            )

    @pytest.mark.parametrize("code", ["lower", "1ABC", "AB", "HAS SPACE"])
    def test_alarm_code_shape(self, code: str) -> None:
        with pytest.raises(ValidationError):
            AlarmTypeRef(alarm_code=code, label="x", domain="CORE", default_severity="INFO")
