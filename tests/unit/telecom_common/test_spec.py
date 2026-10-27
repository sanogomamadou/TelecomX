"""Tests for contract parsing (``parse_avsc``) and the contract model."""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from telecom_common.contracts import (
    ConstraintKind,
    ContractError,
    ContractSpec,
    FieldSpec,
    FieldType,
    parse_avsc,
)
from telecom_common.contracts.spec import ENVELOPE_FIELDS, TOKEN_TARGETS
from telecom_common.domain.enums import PiiClass

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
AVRO_DIR = REPO_ROOT / "data-platform" / "schemas" / "avro"
TS = {"type": "long", "logicalType": "timestamp-micros"}


def _schema(*extra_fields: dict[str, Any], **record: Any) -> dict[str, Any]:
    """A minimal valid contract: the envelope plus ``extra_fields``."""
    schema: dict[str, Any] = {
        "type": "record",
        "name": "Probe",
        "namespace": "com.telecomx.test.v1",
        "doc": "A probe contract.",
        "telecomx.owner": "tests",
        "telecomx.topic": "probe.events",
        "telecomx.partition_key": "event_id",
        "telecomx.sla_freshness_seconds": 60,
        "telecomx.compatibility": "BACKWARD",
        "telecomx.classification": "P4",
        "fields": [
            {"name": "event_id", "type": "string"},
            {"name": "event_timestamp", "type": TS},
            {"name": "ingestion_timestamp", "type": TS},
            {"name": "schema_version", "type": "string", "default": "1.0.0"},
            {"name": "producer_id", "type": "string"},
            {"name": "tenant_id", "type": "string"},
            *extra_fields,
        ],
    }
    schema.update(record)
    return schema


def _parse(*extra_fields: dict[str, Any], **record: Any) -> ContractSpec:
    return parse_avsc("probe-v1", _schema(*extra_fields, **record))


def _field(spec: ContractSpec, name: str) -> FieldSpec:
    return spec.field(name)


class TestMinimalContract:
    def test_parses(self) -> None:
        spec = _parse()
        assert spec.contract_id == "probe-v1"
        assert spec.topic == "probe.events"
        assert len(spec.fields) == 6

    def test_envelope_fields_get_a_canonical_doc(self) -> None:
        """Documented once centrally instead of eleven times in the contracts."""
        spec = _parse()
        assert "Deduplication" in _field(spec, "event_id").doc
        assert all(f.doc for f in spec.fields)

    def test_envelope_flag(self) -> None:
        spec = _parse({"name": "x", "type": "int", "doc": "d"})
        assert _field(spec, "event_id").is_envelope
        assert not _field(spec, "x").is_envelope

    def test_required_means_no_default(self) -> None:
        spec = _parse()
        assert _field(spec, "event_id").is_required
        assert not _field(spec, "schema_version").is_required


class TestTypeResolution:
    @pytest.mark.parametrize(
        ("avro", "expected"),
        [
            ("string", FieldType.STRING),
            ("int", FieldType.INT),
            ("long", FieldType.LONG),
            ("float", FieldType.FLOAT),
            ("double", FieldType.DOUBLE),
            ("boolean", FieldType.BOOLEAN),
            ({"type": "long"}, FieldType.LONG),
            (TS, FieldType.TIMESTAMP),
        ],
    )
    def test_resolves(self, avro: Any, expected: FieldType) -> None:
        spec = _parse({"name": "x", "type": avro, "doc": "d"})
        assert _field(spec, "x").type is expected

    def test_decimal_carries_precision_and_scale(self) -> None:
        decimal = {"type": "bytes", "logicalType": "decimal", "precision": 18, "scale": 2}
        fld = _field(_parse({"name": "x", "type": decimal, "doc": "d"}), "x")
        assert (fld.type, fld.precision, fld.scale) == (FieldType.DECIMAL, 18, 2)

    def test_enum_carries_name_and_symbols(self) -> None:
        enum = {"type": "enum", "name": "Color", "symbols": ["RED", "BLUE"]}
        fld = _field(_parse({"name": "x", "type": enum, "doc": "d"}), "x")
        assert (fld.type, fld.enum_name, fld.enum_symbols) == (
            FieldType.ENUM,
            "Color",
            ("RED", "BLUE"),
        )

    def test_nullable_union_is_unwrapped(self) -> None:
        fld = _field(
            _parse({"name": "x", "type": ["null", "double"], "default": None, "doc": "d"}), "x"
        )
        assert fld.type is FieldType.DOUBLE
        assert fld.nullable

    def test_nullable_enum_and_timestamp(self) -> None:
        enum = {"type": "enum", "name": "Color", "symbols": ["RED"]}
        spec = _parse(
            {"name": "e", "type": ["null", enum], "default": None, "doc": "d"},
            {"name": "t", "type": ["null", TS], "default": None, "doc": "d"},
        )
        assert (_field(spec, "e").type, _field(spec, "e").nullable) == (FieldType.ENUM, True)
        assert (_field(spec, "t").type, _field(spec, "t").nullable) == (FieldType.TIMESTAMP, True)

    @pytest.mark.parametrize(
        "bad_type",
        [
            "bytes",  # raw bytes are not used by the platform
            "UnknownRecord",  # a named-type reference
            ["null", "string", "int"],  # multi-branch union
            ["string", "int"],  # union without null
            {"type": "array", "items": "string"},
            {"type": "map", "values": "string"},
            {"type": "long", "logicalType": "date"},
        ],
    )
    def test_rejects_unsupported_types(self, bad_type: Any) -> None:
        with pytest.raises(ContractError, match=r"unsupported|only \['null'"):
            _parse({"name": "x", "type": bad_type, "doc": "d", "default": None})

    def test_rejects_decimal_without_precision(self) -> None:
        with pytest.raises(ContractError, match="precision and scale"):
            _parse({"name": "x", "type": {"type": "bytes", "logicalType": "decimal"}, "doc": "d"})

    def test_rejects_enum_without_symbols(self) -> None:
        with pytest.raises(ContractError, match="non-empty symbol list"):
            _parse({"name": "x", "type": {"type": "enum", "name": "E", "symbols": []}, "doc": "d"})

    @pytest.mark.parametrize("symbol", ["class", "9LIVES", "has space", "pass"])
    def test_rejects_enum_symbol_that_is_not_a_python_identifier(self, symbol: str) -> None:
        enum = {"type": "enum", "name": "E", "symbols": [symbol]}
        with pytest.raises(ContractError, match="not a usable Python identifier"):
            _parse({"name": "x", "type": enum, "doc": "d"})


class TestDefaults:
    def test_nullable_field_must_declare_null_default(self) -> None:
        with pytest.raises(ContractError, match="must declare 'default': null"):
            _parse({"name": "x", "type": ["null", "string"], "doc": "d"})

    def test_nullable_default_must_be_null(self) -> None:
        with pytest.raises(ContractError, match="default must be null"):
            _parse({"name": "x", "type": ["null", "string"], "default": "a", "doc": "d"})

    def test_enum_default_must_be_a_symbol(self) -> None:
        enum = {"type": "enum", "name": "E", "symbols": ["A", "B"]}
        with pytest.raises(ContractError, match="is not one of"):
            _parse({"name": "x", "type": enum, "default": "Z", "doc": "d"})

    def test_valid_enum_default(self) -> None:
        enum = {"type": "enum", "name": "E", "symbols": ["A", "B"]}
        fld = _field(_parse({"name": "x", "type": enum, "default": "B", "doc": "d"}), "x")
        assert (fld.has_default, fld.default) == (True, "B")


class TestDocumentation:
    def test_business_field_without_doc_is_rejected(self) -> None:
        with pytest.raises(ContractError, match="x: missing 'doc'"):
            _parse({"name": "x", "type": "int"})

    def test_whitespace_doc_counts_as_missing(self) -> None:
        with pytest.raises(ContractError, match="missing 'doc'"):
            _parse({"name": "x", "type": "int", "doc": "   "})


class TestPii:
    def test_pii_without_tokenization_is_rejected(self) -> None:
        """Adding a sensitive field without protecting it must fail the build."""
        with pytest.raises(ContractError, match=r"requires telecomx\.tokenize_as"):
            _parse({"name": "msisdn", "type": "string", "doc": "d", "telecomx.pii": "direct"})

    def test_tokenize_as_satisfies_the_rule(self) -> None:
        spec = _parse(
            {
                "name": "msisdn",
                "type": "string",
                "doc": "d",
                "telecomx.pii": "direct",
                "telecomx.tokenize_as": "subscriber_token",
            }
        )
        assert spec.pii_fields[0].name == "msisdn"
        assert spec.pii_fields[0].pii is PiiClass.DIRECT

    def test_transform_satisfies_the_rule(self) -> None:
        spec = _parse(
            {
                "name": "ip",
                "type": "string",
                "doc": "d",
                "telecomx.pii": "indirect",
                "telecomx.transform": "truncate_ip_24",
            }
        )
        assert spec.pii_fields[0].transform == "truncate_ip_24"

    def test_unknown_token_kind_is_rejected(self) -> None:
        """A closed set: a typo must not create a new, unreviewed kind of token."""
        with pytest.raises(ContractError, match="unknown token kind"):
            _parse(
                {
                    "name": "msisdn",
                    "type": "string",
                    "doc": "d",
                    "telecomx.pii": "direct",
                    "telecomx.tokenize_as": "subscriber_tokn",
                }
            )

    def test_invalid_pii_value_is_rejected(self) -> None:
        with pytest.raises(ContractError, match=r"invalid telecomx\.pii"):
            _parse({"name": "x", "type": "string", "doc": "d", "telecomx.pii": "sensitive"})

    def test_none_pii_is_not_listed_as_pii_field(self) -> None:
        spec = _parse({"name": "x", "type": "string", "doc": "d", "telecomx.pii": "none"})
        assert spec.pii_fields == ()


class TestConstraintApplicability:
    @pytest.mark.parametrize(
        ("avro", "constraint", "message"),
        [
            ("string", "gte:0", "needs a numeric field"),
            ("string", "lt:5", "needs a numeric field"),
            ("boolean", "gte:0", "needs a numeric field"),
            ("int", "pattern:^a$", "needs a string field"),
            ("string", "not_future", "needs a timestamp field"),
            ("int", "not_future", "needs a timestamp field"),
            ("string", "eq_sum:a+b", "needs a numeric field"),
            ("string", "lte_ref:cells.capacity", "needs a numeric field"),
        ],
    )
    def test_constraint_on_wrong_type(self, avro: str, constraint: str, message: str) -> None:
        with pytest.raises(ContractError, match=message):
            _parse({"name": "x", "type": avro, "doc": "d", "telecomx.constraint": constraint})

    def test_not_null_contradicts_nullable(self) -> None:
        with pytest.raises(ContractError, match="contradicts the nullable"):
            _parse(
                {
                    "name": "x",
                    "type": ["null", "string"],
                    "default": None,
                    "doc": "d",
                    "telecomx.constraint": "not_null",
                }
            )

    @pytest.mark.parametrize(
        "constraint",
        ["gte:10,lte:5", "gt:5,lt:5", "gte:5,lt:5", "gt:5,lte:5", "gte:6,lt:5"],
    )
    def test_incompatible_bounds_are_rejected(self, constraint: str) -> None:
        with pytest.raises(ContractError, match="incompatible with upper bound"):
            _parse({"name": "x", "type": "int", "doc": "d", "telecomx.constraint": constraint})

    @pytest.mark.parametrize("constraint", ["gte:5,lte:5", "gte:0,lte:100", "gt:0,lt:2"])
    def test_compatible_bounds_are_accepted(self, constraint: str) -> None:
        _parse({"name": "x", "type": "int", "doc": "d", "telecomx.constraint": constraint})

    def test_bad_constraint_syntax_names_the_field(self) -> None:
        with pytest.raises(ContractError, match=r"probe-v1\.x: unknown constraint"):
            _parse({"name": "x", "type": "int", "doc": "d", "telecomx.constraint": "bogus"})

    def test_eq_sum_operand_must_exist(self) -> None:
        with pytest.raises(ContractError, match="operand 'missing'"):
            _parse(
                {"name": "a", "type": "double", "doc": "d"},
                {
                    "name": "t",
                    "type": "double",
                    "doc": "d",
                    "telecomx.constraint": "eq_sum:a+missing",
                },
            )

    def test_eq_sum_operand_must_be_numeric(self) -> None:
        with pytest.raises(ContractError, match="operand 's'"):
            _parse(
                {"name": "a", "type": "double", "doc": "d"},
                {"name": "s", "type": "string", "doc": "d"},
                {"name": "t", "type": "double", "doc": "d", "telecomx.constraint": "eq_sum:a+s"},
            )

    def test_sum_rules_are_exposed(self) -> None:
        spec = _parse(
            {"name": "a", "type": "double", "doc": "d"},
            {"name": "b", "type": "double", "doc": "d"},
            {"name": "t", "type": "double", "doc": "d", "telecomx.constraint": "eq_sum:a+b"},
        )
        assert spec.sum_rules == (("t", ("a", "b")),)

    def test_constraint_lookup(self) -> None:
        spec = _parse({"name": "x", "type": "int", "doc": "d", "telecomx.constraint": "gte:3"})
        constraint = _field(spec, "x").constraint(ConstraintKind.GTE)
        assert constraint is not None
        assert constraint.value == 3
        assert _field(spec, "x").constraint(ConstraintKind.LTE) is None


class TestRecordLevelRules:
    def test_not_a_record(self) -> None:
        with pytest.raises(ContractError, match="must be a record"):
            parse_avsc("probe-v1", {"type": "enum", "name": "X", "symbols": ["A"]})

    def test_no_fields(self) -> None:
        with pytest.raises(ContractError, match="no fields declared"):
            _parse(**{"fields": []})

    @pytest.mark.parametrize(
        "key",
        [
            "telecomx.owner",
            "telecomx.topic",
            "telecomx.partition_key",
            "telecomx.sla_freshness_seconds",
            "telecomx.compatibility",
            "telecomx.classification",
            "name",
            "namespace",
            "doc",
        ],
    )
    def test_missing_metadata(self, key: str) -> None:
        schema = _schema()
        del schema[key]
        with pytest.raises(ContractError, match=f"missing record metadata '{key}'"):
            parse_avsc("probe-v1", schema)

    def test_invalid_compatibility(self) -> None:
        with pytest.raises(ContractError, match="invalid compatibility"):
            _parse(**{"telecomx.compatibility": "SIDEWAYS"})

    def test_invalid_classification(self) -> None:
        with pytest.raises(ContractError, match="invalid classification"):
            _parse(**{"telecomx.classification": "P9"})

    @pytest.mark.parametrize("missing", sorted(ENVELOPE_FIELDS))
    def test_missing_envelope_field(self, missing: str) -> None:
        schema = _schema()
        schema["fields"] = [f for f in schema["fields"] if f["name"] != missing]
        schema["telecomx.partition_key"] = "producer_id" if missing != "producer_id" else "event_id"
        with pytest.raises(ContractError, match="missing mandatory envelope fields"):
            parse_avsc("probe-v1", schema)

    def test_duplicate_field_names(self) -> None:
        with pytest.raises(ContractError, match="duplicate field names"):
            _parse(
                {"name": "x", "type": "int", "doc": "d"}, {"name": "x", "type": "int", "doc": "d"}
            )

    @pytest.mark.parametrize("name", ["class", "9x", "has-dash", ""])
    def test_invalid_field_name(self, name: str) -> None:
        with pytest.raises(ContractError, match="invalid field name"):
            _parse({"name": name, "type": "int", "doc": "d"})

    def test_partition_key_must_be_a_field(self) -> None:
        with pytest.raises(ContractError, match="partition key 'nope'"):
            _parse(**{"telecomx.partition_key": "nope"})

    def test_unknown_field_lookup_raises(self) -> None:
        with pytest.raises(KeyError):
            _parse().field("nope")


class TestSerialisation:
    def test_round_trips_through_dict(self) -> None:
        spec = _parse(
            {"name": "x", "type": "int", "doc": "d", "telecomx.constraint": "gte:0,lte:9"}
        )
        assert ContractSpec.from_dict("probe-v1", spec.to_dict()) == spec

    def test_survives_json(self) -> None:
        spec = _parse({"name": "x", "type": "int", "doc": "d", "telecomx.constraint": "gte:0"})
        restored = ContractSpec.from_dict("probe-v1", json.loads(json.dumps(spec.to_dict())))
        assert restored == spec

    def test_does_not_mutate_its_input(self) -> None:
        schema = _schema({"name": "x", "type": "int", "doc": "d"})
        snapshot = deepcopy(schema)
        parse_avsc("probe-v1", schema)
        assert schema == snapshot


class TestRealContracts:
    @staticmethod
    def _load() -> dict[str, ContractSpec]:
        return {
            p.stem: parse_avsc(p.stem, json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(AVRO_DIR.glob("*.avsc"))
        }

    def test_all_eleven_contracts_parse(self) -> None:
        assert len(self._load()) == 11

    def test_every_contract_round_trips(self) -> None:
        for contract_id, spec in self._load().items():
            assert ContractSpec.from_dict(contract_id, spec.to_dict()) == spec

    def test_every_pii_field_is_protected(self) -> None:
        for spec in self._load().values():
            for fld in spec.pii_fields:
                assert fld.tokenize_as or fld.transform, f"{spec.contract_id}.{fld.name}"

    def test_every_token_kind_is_a_known_target(self) -> None:
        kinds = {
            fld.tokenize_as
            for spec in self._load().values()
            for fld in spec.fields
            if fld.tokenize_as
        }
        assert kinds <= TOKEN_TARGETS

    def test_topics_are_unique(self) -> None:
        topics = [s.topic for s in self._load().values()]
        assert len(topics) == len(set(topics))

    def test_every_contract_carries_the_envelope(self) -> None:
        for spec in self._load().values():
            assert {f.name for f in spec.fields} >= ENVELOPE_FIELDS


class TestEnvelopeDriftGuard:
    """The standalone validator script and the library each define the envelope.

    The script is stdlib-only by design (it runs in pre-commit with no install), so
    it cannot import the library. This test is what keeps the two lists equal.
    """

    def test_script_and_library_agree(self) -> None:
        path = REPO_ROOT / "scripts" / "validate_contracts.py"
        module_spec = importlib.util.spec_from_file_location("validate_contracts", path)
        assert module_spec is not None
        assert module_spec.loader is not None
        module = importlib.util.module_from_spec(module_spec)
        # @dataclass looks its module up in sys.modules while the class is built, so a
        # module loaded by path must be registered before it is executed.
        sys.modules["validate_contracts"] = module
        try:
            module_spec.loader.exec_module(module)
            assert frozenset(module.ENVELOPE_FIELDS) == ENVELOPE_FIELDS
        finally:
            sys.modules.pop("validate_contracts", None)
