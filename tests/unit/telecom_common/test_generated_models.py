"""Tests that every generated model implements its contract.

Data-driven: for each of the 11 contracts, a valid event and many invalid ones are
fabricated from the *specification* (see ``contract_samples``), then the generated
model is checked against them. A contract edited tomorrow, or a rule added, is
covered without writing a new test by hand.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import fastavro
import pytest
from contract_samples import invalid_variants, valid_sample
from pydantic import ValidationError

from telecom_common.contracts import ContractSpec, load_manifest
from telecom_common.contracts.generated import BY_TOPIC, MODELS
from telecom_common.domain.enums import Technology
from telecom_common.domain.identifiers import CELL_ID_PATTERN, SITE_ID_PATTERN
from telecom_common.timeutils import utc_now

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
AVRO_DIR = REPO_ROOT / "data-platform" / "schemas" / "avro"

SPECS: dict[str, ContractSpec] = load_manifest()
CONTRACT_IDS = sorted(SPECS)


def _all_invalid_cases() -> Iterator[Any]:
    now = utc_now()
    for contract_id in CONTRACT_IDS:
        spec = SPECS[contract_id]
        valid = valid_sample(spec, now)
        for label, event in invalid_variants(spec, valid, now):
            yield pytest.param(contract_id, event, id=f"{contract_id}::{label}")


class TestRegistry:
    def test_every_contract_has_a_model(self) -> None:
        assert set(MODELS) == set(SPECS)

    def test_eleven_contracts(self) -> None:
        assert len(MODELS) == 11

    def test_topics_are_unique_and_match_the_contract(self) -> None:
        assert len(BY_TOPIC) == len(MODELS)
        for contract_id, model in MODELS.items():
            assert BY_TOPIC[SPECS[contract_id].topic] is model
            assert model.__topic__ == SPECS[contract_id].topic
            assert model.__contract__ == contract_id

    def test_partition_key_is_a_field_of_the_model(self) -> None:
        for model in MODELS.values():
            assert model.__partition_key__ in model.model_fields

    def test_model_fields_match_the_contract_exactly(self) -> None:
        """No field missing, none invented."""
        for contract_id, model in MODELS.items():
            assert list(model.model_fields) == [f.name for f in SPECS[contract_id].fields]


@pytest.mark.parametrize("contract_id", CONTRACT_IDS)
class TestValidEvents:
    def test_the_valid_sample_is_accepted(self, contract_id: str) -> None:
        """A model that rejects a spec-compliant event is stricter than its contract."""
        spec = SPECS[contract_id]
        MODELS[contract_id].model_validate(valid_sample(spec))

    def test_required_fields_alone_are_enough(self, contract_id: str) -> None:
        """Optional fields really are optional: their defaults must fill the gap."""
        spec = SPECS[contract_id]
        sample = valid_sample(spec)
        required_only = {f.name: sample[f.name] for f in spec.fields if f.is_required}
        MODELS[contract_id].model_validate(required_only)

    def test_defaults_match_the_contract(self, contract_id: str) -> None:
        spec = SPECS[contract_id]
        sample = valid_sample(spec)
        required_only = {f.name: sample[f.name] for f in spec.fields if f.is_required}
        event = MODELS[contract_id].model_validate(required_only)
        for fld in spec.fields:
            if fld.has_default:
                assert getattr(event, fld.name) == fld.default, fld.name

    def test_json_schema_exposes_units_and_pii(self, contract_id: str) -> None:
        schema = MODELS[contract_id].model_json_schema()["properties"]
        for fld in SPECS[contract_id].fields:
            if fld.unit:
                assert schema[fld.name]["unit"] == fld.unit, fld.name
            if fld.pii.requires_tokenization:
                assert schema[fld.name]["pii"] == fld.pii.value, fld.name


class TestInvalidEvents:
    @pytest.mark.parametrize(("contract_id", "event"), list(_all_invalid_cases()))
    def test_each_broken_rule_is_rejected(self, contract_id: str, event: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            MODELS[contract_id].model_validate(event)

    def test_the_helper_really_produces_many_cases(self) -> None:
        """Guards against the data-driven test silently collapsing to a handful."""
        assert len(list(_all_invalid_cases())) > 400


@pytest.mark.parametrize("contract_id", CONTRACT_IDS)
class TestAvroInteroperability:
    """The model and the .avsc must agree on the wire, not merely on paper."""

    @staticmethod
    def _roundtrip(contract_id: str) -> tuple[Any, Any]:
        schema = fastavro.parse_schema(
            json.loads((AVRO_DIR / f"{contract_id}.avsc").read_text(encoding="utf-8"))
        )
        model = MODELS[contract_id]
        original = model.model_validate(valid_sample(SPECS[contract_id]))

        buffer = io.BytesIO()
        fastavro.schemaless_writer(buffer, schema, original.model_dump())
        buffer.seek(0)
        record = fastavro.schemaless_reader(buffer, schema)
        return original, model.model_validate(record)

    def test_a_model_dump_is_serialisable_by_the_avro_schema(self, contract_id: str) -> None:
        self._roundtrip(contract_id)

    def test_write_then_read_gives_back_the_same_event(self, contract_id: str) -> None:
        original, restored = self._roundtrip(contract_id)
        assert restored == original


class TestSpecificBehaviour:
    """Targeted checks that document intent, beyond the data-driven coverage."""

    def _metrics(self, **overrides: Any) -> dict[str, Any]:
        sample = valid_sample(SPECS["network-metrics-v1"])
        sample.update(overrides)
        return sample

    def test_a_degraded_cell_is_a_valid_event(self) -> None:
        """The PRD scenario: CELL-0421 at 14.8 % packet loss is data, not an error."""
        MODELS["network-metrics-v1"].model_validate(
            self._metrics(
                cell_id="CELL-0421", packet_loss_pct=14.8, latency_ms=184.0, drop_rate_pct=12.6
            )
        )

    @pytest.mark.parametrize("site", ["SITE-102", "SITE-1234"])
    def test_site_pattern_accepts_three_and_four_digits(self, site: str) -> None:
        """Regression for the comma inside {3,4}."""
        MODELS["network-metrics-v1"].model_validate(self._metrics(site_id=site))

    @pytest.mark.parametrize("site", ["SITE-12", "SITE-12345", "site-102", "SITE-102X"])
    def test_site_pattern_rejects_other_shapes(self, site: str) -> None:
        with pytest.raises(ValidationError):
            MODELS["network-metrics-v1"].model_validate(self._metrics(site_id=site))

    def test_technology_is_the_domain_enum(self) -> None:
        event = MODELS["network-metrics-v1"].model_validate(self._metrics(technology="G4"))
        assert event.technology is Technology.G4  # type: ignore[attr-defined]

    def test_a_timestamp_in_epoch_microseconds_is_refused(self) -> None:
        """The precise unit confusion the guard exists for."""
        micros = int(utc_now().timestamp() * 1_000_000)
        with pytest.raises(ValidationError, match="ambiguous"):
            MODELS["network-metrics-v1"].model_validate(self._metrics(event_timestamp=micros))

    def test_a_naive_timestamp_is_refused(self) -> None:
        naive = (utc_now() - timedelta(minutes=1)).replace(tzinfo=None)
        with pytest.raises(ValidationError, match="timezone"):
            MODELS["network-metrics-v1"].model_validate(self._metrics(event_timestamp=naive))

    def test_data_session_total_must_equal_download_plus_upload(self) -> None:
        sample = valid_sample(SPECS["data-session-v1"])
        sample.update(download_mb=10.0, upload_mb=5.0, total_volume_mb=15.0)
        MODELS["data-session-v1"].model_validate(sample)
        sample["total_volume_mb"] = 99.0
        with pytest.raises(ValidationError, match="total_volume_mb"):
            MODELS["data-session-v1"].model_validate(sample)

    def test_mobile_money_amount_is_an_exact_decimal(self) -> None:
        """No float anywhere near a financial amount."""
        sample = valid_sample(SPECS["mobilemoney-transaction-v1"])
        sample["amount"] = "950000.00"
        event = MODELS["mobilemoney-transaction-v1"].model_validate(sample)
        assert str(event.amount) == "950000.00"  # type: ignore[attr-defined]
        sample["amount"] = "950000.005"
        with pytest.raises(ValidationError, match="decimal places"):
            MODELS["mobilemoney-transaction-v1"].model_validate(sample)

    def test_a_transaction_of_zero_is_refused(self) -> None:
        sample = valid_sample(SPECS["mobilemoney-transaction-v1"])
        sample["amount"] = "0.00"
        with pytest.raises(ValidationError):
            MODELS["mobilemoney-transaction-v1"].model_validate(sample)

    def test_raw_msisdn_fields_are_flagged_as_pii_in_the_schema(self) -> None:
        schema = MODELS["cdr-v1"].model_json_schema()["properties"]
        assert schema["caller_id"]["pii"] == "direct"
        assert schema["imei"]["pii"] == "indirect"
        assert "pii" not in schema["cell_id"]


class TestPatternParityWithDomainIdentifiers:
    """The contract and the domain identifiers describe the same identifiers.

    They are written in different regex dialects (``[0-9]`` versus ``\\d``), so this
    guards against the two drifting apart.
    """

    @pytest.mark.parametrize(
        ("field", "domain_pattern"),
        [("cell_id", CELL_ID_PATTERN), ("site_id", SITE_ID_PATTERN)],
    )
    @pytest.mark.parametrize(
        "value",
        [
            "CELL-0421",
            "CELL-421",
            "CELL-04211",
            "SITE-102",
            "SITE-1234",
            "SITE-12",
            "cell-0421",
            "XCELL-0421",
            "CELL-0421X",
            "",
            "SITE-",
        ],
    )
    def test_same_verdict(self, field: str, domain_pattern: str, value: str) -> None:
        contract_accepts = True
        try:
            MODELS["network-metrics-v1"].model_validate(
                {**valid_sample(SPECS["network-metrics-v1"]), field: value}
            )
        except ValidationError:
            contract_accepts = False
        assert contract_accepts == bool(re.match(domain_pattern, value))
