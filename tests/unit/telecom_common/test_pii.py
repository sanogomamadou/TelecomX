"""Tests for PII detection and redaction (NFR-SEC-010)."""

from __future__ import annotations

import pytest

from telecom_common.security.pii import (
    Confidence,
    PiiKind,
    contains_pii,
    redact,
    scan,
)

pytestmark = pytest.mark.unit


class TestHighConfidenceDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "Appel depuis +22370123456 vers le support",
            "Numero: 22370123456",
            "contact +223 70 12 34 56 pour suivi",
            "appelant +223-70-12-34-56",
        ],
    )
    def test_detects_international_msisdn(self, text: str) -> None:
        matches = scan(text)
        assert any(m.kind is PiiKind.MSISDN for m in matches)
        assert all(m.confidence is Confidence.HIGH for m in matches)

    def test_detects_email(self) -> None:
        matches = scan("reclamation de awa.traore@example.ml hier")
        assert [m.kind for m in matches] == [PiiKind.EMAIL]
        assert matches[0].value == "awa.traore@example.ml"

    def test_detects_imsi(self) -> None:
        matches = scan("IMSI 610021234567890 detache")
        assert any(m.kind is PiiKind.IMSI for m in matches)


class TestMediumConfidenceDetection:
    def test_detects_bare_local_msisdn(self) -> None:
        matches = scan("abonne 70123456 injoignable")
        assert any(m.kind is PiiKind.MSISDN and m.confidence is Confidence.MEDIUM for m in matches)

    def test_detects_precise_coordinates(self) -> None:
        """Precise location is P2 and must have been generalised to a cell."""
        matches = scan("position 12.639200, -8.002600")
        assert any(m.kind is PiiKind.COORDINATES for m in matches)

    def test_high_confidence_filter_excludes_ambiguous_matches(self) -> None:
        text = "abonne 70123456 injoignable"
        assert scan(text) != []
        assert scan(text, min_confidence=Confidence.HIGH) == []


class TestFalsePositives:
    """Values that merely look like identifiers must not be flagged as HIGH."""

    @pytest.mark.parametrize(
        "text",
        [
            "CELL-0421 en degradation",
            "latence 184 ms, perte 14.8 %",
            "incident INC-2026-009821 resolu",
            "token v1:kJ8fD2mNp4QrS6tUvW8xY0zA2bC4dE6f",
            "snapshot 2026-09-10T18:00:00Z",
        ],
    )
    def test_does_not_flag_platform_identifiers(self, text: str) -> None:
        assert scan(text, min_confidence=Confidence.HIGH) == []

    def test_amount_is_not_high_confidence(self) -> None:
        """An 8-digit XOF amount has the same shape as a local MSISDN."""
        assert scan("montant 95000000 XOF", min_confidence=Confidence.HIGH) == []


class TestContainsPii:
    def test_true_when_identifier_present(self) -> None:
        assert contains_pii("appel de +22370123456")

    def test_false_on_clean_text(self) -> None:
        assert not contains_pii("drop_rate de CELL-0421 a 12.6 %")


class TestRedact:
    def test_replaces_with_typed_placeholder(self) -> None:
        """A redacted line must stay diagnosable, hence the typed placeholder."""
        assert redact("appel de +22370123456 echoue") == "appel de [MSISDN] echoue"

    def test_redacts_several_occurrences(self) -> None:
        result = redact("de +22370123456 vers awa@example.ml")
        assert result == "de [MSISDN] vers [EMAIL]"

    def test_leaves_clean_text_untouched(self) -> None:
        text = "CELL-0421 drop_rate 12.6 %"
        assert redact(text) == text

    def test_redacted_output_contains_no_pii(self) -> None:
        """The property that matters: redaction is complete."""
        text = "appel +22370123456 de awa@example.ml imsi 610021234567890"
        assert not contains_pii(redact(text))


class TestOverlapResolution:
    def test_overlapping_matches_are_claimed_once(self) -> None:
        """An international MSISDN also contains a bare local one."""
        matches = scan("+22370123456")
        assert len(matches) == 1
        assert matches[0].confidence is Confidence.HIGH

    def test_matches_are_ordered_by_position(self) -> None:
        matches = scan("a@b.ml puis +22370123456 puis c@d.ml")
        assert [m.start for m in matches] == sorted(m.start for m in matches)
