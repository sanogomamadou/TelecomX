"""Tests for the generated manifest, the contract view consumed by Spark and dbt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from telecom_common.contracts import (
    ConstraintKind,
    ContractSpec,
    load_manifest,
    parse_avsc,
)
from telecom_common.contracts.manifest import manifest_path_text

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
AVRO_DIR = REPO_ROOT / "data-platform" / "schemas" / "avro"


class TestManifest:
    def test_contains_every_contract(self) -> None:
        assert len(load_manifest()) == 11

    def test_is_exactly_what_the_avsc_files_parse_to(self) -> None:
        """The manifest is derived, never maintained: it must equal a fresh parse."""
        for path in sorted(AVRO_DIR.glob("*.avsc")):
            fresh = parse_avsc(path.stem, json.loads(path.read_text(encoding="utf-8")))
            assert load_manifest()[path.stem] == fresh, path.stem

    def test_is_cached(self) -> None:
        assert load_manifest() is load_manifest()

    def test_is_valid_json_with_a_version(self) -> None:
        document = json.loads(manifest_path_text())
        assert document["manifest_version"] == 1
        assert len(document["contracts"]) == 11

    def test_keeps_the_original_field_order(self) -> None:
        """Order matters to anything building a positional schema from it."""
        for path in sorted(AVRO_DIR.glob("*.avsc")):
            declared = [f["name"] for f in json.loads(path.read_text(encoding="utf-8"))["fields"]]
            assert [f.name for f in load_manifest()[path.stem].fields] == declared


class TestManifestCarriesWhatModelsCannotEnforce:
    """Rules needing outside state are not in the Pydantic models, so they must be here."""

    def test_referential_rules_are_present(self) -> None:
        cell = load_manifest()["network-metrics-v1"].field("cell_id")
        ref = cell.constraint(ConstraintKind.IN_REF)
        assert ref is not None
        assert ref.value == "cells"

    def test_cross_table_bound_is_present(self) -> None:
        users = load_manifest()["network-metrics-v1"].field("active_users")
        bound = users.constraint(ConstraintKind.LTE_REF)
        assert bound is not None
        assert bound.value == "cells.capacity_users"

    def test_uniqueness_is_present_on_every_event_id(self) -> None:
        for spec in load_manifest().values():
            assert spec.field("event_id").constraint(ConstraintKind.UNIQUE) is not None, (
                spec.contract_id
            )

    def test_context_dependent_rules_are_flagged(self) -> None:
        """Lets the Spark job pick exactly the rules it must check itself."""
        needing_context = {
            (spec.contract_id, fld.name, c.kind)
            for spec in load_manifest().values()
            for fld in spec.fields
            for c in fld.constraints
            if c.needs_context
        }
        kinds = {kind for _, _, kind in needing_context}
        assert kinds == {
            ConstraintKind.UNIQUE,
            ConstraintKind.IN_REF,
            ConstraintKind.LTE_REF,
        }


class TestPiiDrivesTokenization:
    """The Spark silver job will tokenize exactly what this manifest lists (ADR-013)."""

    def test_every_pii_field_names_its_protection(self) -> None:
        for spec in load_manifest().values():
            for fld in spec.pii_fields:
                assert fld.tokenize_as or fld.transform, f"{spec.contract_id}.{fld.name}"

    def test_the_cdr_lists_all_four_direct_and_indirect_identifiers(self) -> None:
        cdr = load_manifest()["cdr-v1"]
        assert {f.name for f in cdr.pii_fields} == {"caller_id", "callee_id", "imsi", "imei"}

    def test_no_event_without_a_pii_field_carries_a_tokenization_target(self) -> None:
        for spec in load_manifest().values():
            for fld in spec.fields:
                if fld.tokenize_as:
                    assert fld.pii.requires_tokenization, f"{spec.contract_id}.{fld.name}"

    def test_network_contracts_carry_no_pii(self) -> None:
        """Pure infrastructure telemetry must stay free of personal data."""
        for contract_id in ("network-metrics-v1", "network-alarms-v1", "equipment-telemetry-v1"):
            assert load_manifest()[contract_id].pii_fields == (), contract_id

    def test_subscriber_facing_contracts_do_carry_pii(self) -> None:
        for contract_id in ("cdr-v1", "sms-v1", "recharge-v1", "mobilemoney-transaction-v1"):
            assert load_manifest()[contract_id].pii_fields, contract_id


class TestPartitionKeys:
    def test_partition_keys_exist_in_every_contract(self) -> None:
        for spec in load_manifest().values():
            assert spec.partition_key in {f.name for f in spec.fields}, spec.contract_id

    def test_subscriber_keyed_topics_use_a_raw_identifier(self) -> None:
        """Documents a finding rather than hiding it.

        The producer holds only the raw identifier, so a Kafka key cannot be a token
        (tokenization happens later, in the silver job). Raw MSISDNs therefore sit in
        Kafka message keys. That is the same trust zone as bronze; see ADR-013.
        """
        spec: ContractSpec = load_manifest()["cdr-v1"]
        assert spec.field(spec.partition_key).pii.requires_tokenization
