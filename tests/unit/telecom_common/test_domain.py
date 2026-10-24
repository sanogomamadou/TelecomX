"""Tests for domain identifiers and cross-cutting enumerations."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from telecom_common.domain import (
    CellId,
    DataZone,
    PiiClass,
    Region,
    RiskClass,
    SiteId,
    SubscriberToken,
    Technology,
    TenantId,
    format_cell_id,
    format_site_id,
    is_valid_identifier,
)

pytestmark = pytest.mark.unit


class _Probe(BaseModel):
    """Minimal model used to exercise the constrained types through pydantic."""

    cell: CellId
    site: SiteId
    token: SubscriberToken


VALID = {
    "cell": "CELL-0421",
    "site": "SITE-102",
    "token": "v1:kJ8fD2mNp4QrS6tUvW8xY0zA2bC4dE6f",
}


class TestConstrainedTypes:
    def test_accepts_well_formed_identifiers(self) -> None:
        probe = _Probe(**VALID)
        assert probe.cell == "CELL-0421"

    @pytest.mark.parametrize(
        "bad_cell",
        [
            "CELL-421",  # three digits
            "CELL-04211",  # five digits
            "cell-0421",  # lowercase
            "XCELL-0421",  # unanchored prefix
            "CELL-0421X",  # unanchored suffix
            "0421",  # no prefix
            "",
        ],
    )
    def test_rejects_malformed_cell_id(self, bad_cell: str) -> None:
        """Rejection happens at the edge, not three layers downstream."""
        with pytest.raises(ValidationError):
            _Probe(**{**VALID, "cell": bad_cell})

    @pytest.mark.parametrize(
        "bad_token",
        [
            "+22370123456",  # a raw MSISDN must never pass as a token
            "kJ8fD2mNp4QrS6tUvW8xY0zA2bC4dE6f",  # missing version prefix
            "v1:tooshort",
            "v1:kJ8fD2mNp4QrS6tUvW8xY0zA2bC4dE6f+",  # non url-safe character
        ],
    )
    def test_rejects_malformed_token(self, bad_token: str) -> None:
        with pytest.raises(ValidationError):
            _Probe(**{**VALID, "token": bad_token})


class TestIsValidIdentifier:
    @pytest.mark.parametrize(
        ("kind", "value"),
        [
            ("cell", "CELL-0421"),
            ("site", "SITE-102"),
            ("site", "SITE-1610"),
            ("equipment", "ROUTER-082"),
            ("equipment", "BBU-014"),
            ("equipment", "POWER_SUPPLY-201"),
            ("call", "CALL-928372"),
            ("alarm", "ALM-20261024-000417"),
            ("transaction", "TRX-20261024-00093812"),
            ("incident", "INC-2026-009821"),
        ],
    )
    def test_accepts_valid(self, kind: str, value: str) -> None:
        assert is_valid_identifier(kind, value)

    @pytest.mark.parametrize(
        ("kind", "value"),
        [("cell", "SITE-102"), ("site", "CELL-0421"), ("incident", "INC-26-009821")],
    )
    def test_rejects_invalid(self, kind: str, value: str) -> None:
        assert not is_valid_identifier(kind, value)

    def test_unknown_kind_raises_rather_than_returning_false(self) -> None:
        """A caller typo must not silently degrade into 'not valid'."""
        with pytest.raises(KeyError):
            is_valid_identifier("celll", "CELL-0421")


class TestFormatHelpers:
    @pytest.mark.parametrize(
        ("number", "expected"), [(421, "CELL-0421"), (0, "CELL-0000"), (9999, "CELL-9999")]
    )
    def test_format_cell_id(self, number: int, expected: str) -> None:
        assert format_cell_id(number) == expected

    def test_format_cell_id_output_is_valid(self) -> None:
        assert is_valid_identifier("cell", format_cell_id(421))

    def test_format_site_id_output_is_valid(self) -> None:
        assert is_valid_identifier("site", format_site_id(102))

    @pytest.mark.parametrize("number", [-1, 10_000])
    def test_cell_id_rejects_out_of_range(self, number: int) -> None:
        with pytest.raises(ValueError, match=r"\[0, 9999\]"):
            format_cell_id(number)

    @pytest.mark.parametrize("number", [-1, 10_000])
    def test_site_id_rejects_out_of_range(self, number: int) -> None:
        with pytest.raises(ValueError, match=r"\[0, 9999\]"):
            format_site_id(number)


class TestTechnology:
    @pytest.mark.parametrize(
        ("tech", "label", "generation"),
        [(Technology.G2, "2G", 2), (Technology.G4, "4G", 4), (Technology.G5, "5G", 5)],
    )
    def test_label_and_generation(self, tech: Technology, label: str, generation: int) -> None:
        assert tech.label == label
        assert tech.generation == generation

    def test_wire_value_is_avro_compatible(self) -> None:
        """Avro enum symbols cannot start with a digit, hence the G prefix."""
        assert all(not t.value[0].isdigit() for t in Technology)


class TestRegion:
    def test_has_nine_regions(self) -> None:
        assert len(Region) == 9

    def test_codes_are_unique_and_three_letters(self) -> None:
        codes = [r.value for r in Region]
        assert len(set(codes)) == len(codes)
        assert all(len(c) == 3 for c in codes)

    def test_label(self) -> None:
        assert Region.TOMBOUCTOU.label == "Tombouctou"


class TestRiskClass:
    @pytest.mark.parametrize(
        ("risk", "level"),
        [
            (RiskClass.L1_READ, 1),
            (RiskClass.L2_LOW, 2),
            (RiskClass.L3_MEDIUM, 3),
            (RiskClass.L4_HIGH, 4),
            (RiskClass.L5_FORBIDDEN, 5),
        ],
    )
    def test_level(self, risk: RiskClass, level: int) -> None:
        assert risk.level == level

    @pytest.mark.parametrize(
        ("risk", "expected"),
        [
            (RiskClass.L1_READ, False),
            (RiskClass.L2_LOW, False),
            (RiskClass.L3_MEDIUM, True),
            (RiskClass.L4_HIGH, True),
        ],
    )
    def test_requires_approval_from_l3(self, risk: RiskClass, expected: bool) -> None:
        """The autonomy model hinges on this boundary (PRD section 7)."""
        assert risk.requires_approval is expected

    def test_dual_approval_only_from_l4(self) -> None:
        assert not RiskClass.L3_MEDIUM.requires_dual_approval
        assert RiskClass.L4_HIGH.requires_dual_approval

    def test_l5_is_never_exposable(self) -> None:
        """L5 must not appear in a catalogue whatever the caller's permissions."""
        assert not RiskClass.L5_FORBIDDEN.is_exposable
        assert all(r.is_exposable for r in RiskClass if r is not RiskClass.L5_FORBIDDEN)


class TestPiiClass:
    def test_none_needs_no_tokenization(self) -> None:
        assert not PiiClass.NONE.requires_tokenization

    @pytest.mark.parametrize("pii", [PiiClass.DIRECT, PiiClass.INDIRECT])
    def test_sensitive_classes_require_tokenization(self, pii: PiiClass) -> None:
        assert pii.requires_tokenization

    def test_values_match_contract_attribute(self) -> None:
        """Values mirror telecomx.pii in data-platform/schemas/avro/."""
        assert {p.value for p in PiiClass} == {"none", "direct", "indirect"}


class TestDataZoneAndTenant:
    def test_zones(self) -> None:
        assert [z.value for z in DataZone] == ["bronze", "silver", "gold"]

    def test_tenants(self) -> None:
        assert TenantId.MALI.value == "telecomx-ml"
        assert len(TenantId) == 3
