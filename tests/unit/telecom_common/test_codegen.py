"""Tests for the code generator and its command-line wrapper."""

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from telecom_common.contracts import ContractError, ContractModel, parse_avsc
from telecom_common.contracts.codegen import (
    GENERATED_MARKER,
    SHARED_ENUMS,
    is_generated,
    module_name,
    render_manifest,
    render_module,
    render_registry,
    validate_specs,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
TS = {"type": "long", "logicalType": "timestamp-micros"}


def _load_generate_script() -> ModuleType:
    """Import data-platform/schemas/generate.py (its directory name is not importable)."""
    path = REPO_ROOT / "data-platform" / "schemas" / "generate.py"
    module_spec = importlib.util.spec_from_file_location("telecomx_generate", path)
    assert module_spec is not None
    assert module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules["telecomx_generate"] = module
    module_spec.loader.exec_module(module)
    return module


generate = _load_generate_script()


def _schema(*extra_fields: dict[str, Any], **record: Any) -> dict[str, Any]:
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


def _import_rendered(source: str, tmp_path: Path, name: str = "probe_contract") -> ModuleType:
    """Write rendered source to disk and import it as a real module.

    Pydantic resolves annotations through ``sys.modules``, so the module must be
    registered rather than merely ``exec``'d.
    """
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    module_spec = importlib.util.spec_from_file_location(name, path)
    assert module_spec is not None
    assert module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    try:
        module_spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


class TestGeneratedCodeIsUpToDate:
    """The gate that makes a stale or hand-edited generated file impossible to merge."""

    def test_committed_files_match_what_the_contracts_produce(self) -> None:
        outputs = generate.build_outputs(generate.DEFAULT_SCHEMA_DIR, generate.DEFAULT_OUT_DIR)
        assert len(outputs) == 13  # 11 models + registry + manifest
        for path, expected in outputs.items():
            assert path.is_file(), f"{path.name} is missing: run `make codegen`"
            assert path.read_bytes().decode("utf-8") == expected, (
                f"{path.name} is out of date: run `make codegen`"
            )

    def test_no_stale_generated_module_remains(self) -> None:
        outputs = generate.build_outputs(generate.DEFAULT_SCHEMA_DIR, generate.DEFAULT_OUT_DIR)
        assert generate.stale_files(generate.DEFAULT_OUT_DIR, set(outputs)) == []

    def test_every_generated_module_carries_the_marker(self) -> None:
        outputs = generate.build_outputs(generate.DEFAULT_SCHEMA_DIR, generate.DEFAULT_OUT_DIR)
        for path, content in outputs.items():
            if path.suffix == ".py":
                assert content.startswith(GENERATED_MARKER), path.name

    def test_generated_files_use_lf_line_endings(self) -> None:
        """The repository is LF everywhere, including on Windows."""
        files = [p for p in generate.DEFAULT_OUT_DIR.glob("*") if p.is_file()]
        assert len(files) >= 13  # a vacuous pass on an empty list would prove nothing
        for path in files:
            assert b"\r\n" not in path.read_bytes(), path.name


class TestDeterminism:
    def test_rendering_twice_gives_identical_output(self) -> None:
        first = generate.build_outputs(generate.DEFAULT_SCHEMA_DIR, generate.DEFAULT_OUT_DIR)
        second = generate.build_outputs(generate.DEFAULT_SCHEMA_DIR, generate.DEFAULT_OUT_DIR)
        assert first == second

    def test_manifest_is_independent_of_input_order(self) -> None:
        specs = [
            parse_avsc("b-v1", _schema(**{"telecomx.topic": "b.events", "name": "B"})),
            parse_avsc("a-v1", _schema(**{"telecomx.topic": "a.events", "name": "A"})),
        ]
        assert render_manifest(specs) == render_manifest(list(reversed(specs)))
        assert render_registry(specs) == render_registry(list(reversed(specs)))


class TestRenderedModule:
    def test_is_valid_python(self) -> None:
        source = render_module(parse_avsc("probe-v1", _schema()), source="probe.avsc")
        ast.parse(source)

    def test_header_names_the_source_and_how_to_regenerate(self) -> None:
        source = render_module(parse_avsc("probe-v1", _schema()), source="path/probe.avsc")
        assert "# Source: path/probe.avsc" in source
        assert "make codegen" in source
        assert source.endswith("\n")

    def test_class_carries_contract_metadata(self, tmp_path: Path) -> None:
        source = render_module(parse_avsc("probe-v1", _schema()), source="x")
        model = _import_rendered(source, tmp_path).Probe
        assert issubclass(model, ContractModel)
        assert model.__contract__ == "probe-v1"
        assert model.__topic__ == "probe.events"
        assert model.__partition_key__ == "event_id"

    def test_kitchen_sink_contract_behaves_correctly(self, tmp_path: Path) -> None:
        """Exercises features the real contracts do not all use, end to end."""
        # Two distinct enums: Avro forbids redefining a named type, and the generator
        # rightly refuses an enum declared twice (see TestValidation).
        enum = {"type": "enum", "name": "Color", "symbols": ["RED", "BLUE"]}
        shade = {"type": "enum", "name": "Shade", "symbols": ["RED", "BLUE"]}
        decimal = {"type": "bytes", "logicalType": "decimal", "precision": 10, "scale": 2}
        spec = parse_avsc(
            "probe-v1",
            _schema(
                {"name": "color", "type": enum, "default": "BLUE", "doc": "d"},
                {"name": "maybe_color", "type": ["null", shade], "default": None, "doc": "d"},
                {
                    "name": "ratio",
                    "type": "double",
                    "default": 0.5,
                    "doc": "d",
                    "telecomx.constraint": "gt:0,lt:1",
                },
                {"name": "price", "type": decimal, "doc": "d", "telecomx.constraint": "gte:0"},
                {"name": "when", "type": ["null", TS], "default": None, "doc": "d"},
                {"name": "ok", "type": "boolean", "default": True, "doc": "d"},
                {"name": "count", "type": "long", "default": 3, "doc": "d"},
                {
                    "name": "unit_field",
                    "type": "double",
                    "default": 1.0,
                    "doc": "d",
                    "telecomx.unit": "percent",
                },
            ),
        )
        module = _import_rendered(render_module(spec, source="x"), tmp_path)
        model = module.Probe

        base: dict[str, Any] = {
            "event_id": "e1",
            "event_timestamp": "2026-01-01T00:00:00Z",
            "ingestion_timestamp": "2026-01-01T00:00:01Z",
            "producer_id": "p",
            "tenant_id": "t",
            "price": "10.50",
        }
        event = model(**base)
        assert event.color is module.Color.BLUE  # enum default rendered as a member
        assert event.maybe_color is None
        assert event.ratio == 0.5
        assert event.ok is True
        assert event.count == 3

        with pytest.raises(ValueError, match="greater than 0"):
            model(**{**base, "ratio": 0})
        with pytest.raises(ValueError, match="less than 1"):
            model(**{**base, "ratio": 1})
        with pytest.raises(ValueError, match="decimal places"):
            model(**{**base, "price": "10.505"})
        assert model(**{**base, "maybe_color": "RED"}).maybe_color is module.Shade.RED

        extras = model.model_json_schema()["properties"]
        assert extras["unit_field"]["unit"] == "percent"

    def test_local_enum_is_rendered_as_strenum(self, tmp_path: Path) -> None:
        enum = {"type": "enum", "name": "Color", "symbols": ["RED"]}
        spec = parse_avsc("probe-v1", _schema({"name": "c", "type": enum, "doc": "d"}))
        source = render_module(spec, source="x")
        assert "from enum import StrEnum" in source
        assert "class Color(StrEnum):" in source


class TestSharedEnums:
    def test_domain_technology_is_reused_not_regenerated(self) -> None:
        from telecom_common.contracts.generated import MODELS
        from telecom_common.domain.enums import Technology

        for contract_id in ("network-metrics-v1", "data-session-v1"):
            model = MODELS[contract_id]
            assert model.model_fields["technology"].annotation is Technology

    def test_session_technology_alias_is_shared_too(self) -> None:
        assert SHARED_ENUMS["SessionTechnology"] == SHARED_ENUMS["Technology"]

    def test_divergence_between_contract_and_domain_fails_generation(self) -> None:
        """If one side gains or loses a symbol and the other does not, stop."""
        enum = {"type": "enum", "name": "Technology", "symbols": ["G2", "G3"]}
        spec = parse_avsc("probe-v1", _schema({"name": "t", "type": enum, "doc": "d"}))
        with pytest.raises(ContractError, match="symbols differ"):
            render_module(spec, source="x")

    def test_matching_symbols_are_accepted(self) -> None:
        enum = {"type": "enum", "name": "Technology", "symbols": ["G5", "G4", "G3", "G2"]}
        spec = parse_avsc("probe-v1", _schema({"name": "t", "type": enum, "doc": "d"}))
        assert "from telecom_common.domain.enums import Technology" in render_module(
            spec, source="x"
        )


class TestDocstringEscaping:
    """Contract documentation is free text and must never produce invalid Python."""

    @pytest.mark.parametrize(
        "doc",
        ['He said """hi"""', "back\\slash", 'ends with a quote"', "a 'single' and \"double\""],
    )
    def test_rendered_source_stays_valid(self, doc: str) -> None:
        spec = parse_avsc("probe-v1", _schema(doc=doc))
        tree = ast.parse(render_module(spec, source="x"))
        klass = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        recovered = ast.get_docstring(klass, clean=False)
        assert recovered is not None
        assert recovered.strip() == doc.strip()


class TestValidation:
    def test_duplicate_topic(self) -> None:
        specs = [
            parse_avsc("a-v1", _schema(name="A")),
            parse_avsc("b-v1", _schema(name="B")),
        ]
        with pytest.raises(ContractError, match="duplicate topic"):
            validate_specs(specs)

    def test_duplicate_record_name(self) -> None:
        specs = [
            parse_avsc("a-v1", _schema(**{"telecomx.topic": "a.events"})),
            parse_avsc("b-v1", _schema(**{"telecomx.topic": "b.events"})),
        ]
        with pytest.raises(ContractError, match="duplicate record name"):
            validate_specs(specs)

    def test_duplicate_contract_id(self) -> None:
        spec = parse_avsc("a-v1", _schema())
        with pytest.raises(ContractError, match="duplicate contract id"):
            validate_specs([spec, spec])

    def test_distinct_specs_pass(self) -> None:
        validate_specs(
            [
                parse_avsc("a-v1", _schema(name="A", **{"telecomx.topic": "a.events"})),
                parse_avsc("b-v1", _schema(name="B", **{"telecomx.topic": "b.events"})),
            ]
        )

    def test_enum_shadowing_the_record_name_is_rejected(self) -> None:
        enum = {"type": "enum", "name": "Probe", "symbols": ["A"]}
        spec = parse_avsc("probe-v1", _schema({"name": "c", "type": enum, "doc": "d"}))
        with pytest.raises(ContractError, match="share the name"):
            render_module(spec, source="x")

    def test_same_enum_declared_twice_is_rejected(self) -> None:
        enum = {"type": "enum", "name": "Color", "symbols": ["A"]}
        spec = parse_avsc(
            "probe-v1",
            _schema(
                {"name": "c1", "type": enum, "doc": "d"},
                {"name": "c2", "type": enum, "doc": "d"},
            ),
        )
        with pytest.raises(ContractError, match="declared twice"):
            render_module(spec, source="x")


class TestSmallHelpers:
    def test_module_name(self) -> None:
        assert module_name("network-metrics-v1") == "network_metrics_v1"

    def test_is_generated(self) -> None:
        assert is_generated(GENERATED_MARKER + "\nrest")
        assert not is_generated("# hand written\n")

    def test_registry_lists_every_model_and_topic(self) -> None:
        specs = [
            parse_avsc("a-v1", _schema(name="A", **{"telecomx.topic": "a.events"})),
            parse_avsc("b-v1", _schema(name="B", **{"telecomx.topic": "b.events"})),
        ]
        registry = render_registry(specs)
        for needle in ("'a-v1': A", "'b-v1': B", "'a.events': A", "'b.events': B"):
            assert needle in registry
        ast.parse(registry)

    def test_manifest_is_valid_json_with_all_contracts(self) -> None:
        specs = [parse_avsc("a-v1", _schema(name="A"))]
        document = json.loads(render_manifest(specs))
        assert document["manifest_version"] == 1
        assert list(document["contracts"]) == ["a-v1"]


class TestCommandLine:
    @pytest.fixture
    def workspace(self, tmp_path: Path) -> tuple[Path, Path]:
        schema_dir = tmp_path / "avro"
        shutil.copytree(generate.DEFAULT_SCHEMA_DIR, schema_dir)
        return schema_dir, tmp_path / "generated"

    def _run(self, workspace: tuple[Path, Path], *flags: str) -> int:
        schema_dir, out_dir = workspace
        return int(
            generate.main(["--schema-dir", str(schema_dir), "--out-dir", str(out_dir), *flags])
        )

    def test_generates_then_check_passes(self, workspace: tuple[Path, Path]) -> None:
        assert self._run(workspace) == 0
        assert self._run(workspace, "--check") == 0

    def test_check_fails_when_nothing_was_generated(
        self, workspace: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert self._run(workspace, "--check") == 1
        assert "missing" in capsys.readouterr().err

    def test_check_detects_a_hand_edited_file(
        self, workspace: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._run(workspace)
        target = workspace[1] / "cdr_v1.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
        assert self._run(workspace, "--check") == 1
        stderr = capsys.readouterr().err
        assert "outdated" in stderr
        assert "cdr_v1.py" in stderr

    def test_check_detects_a_contract_changed_without_regenerating(
        self, workspace: tuple[Path, Path]
    ) -> None:
        self._run(workspace)
        contract = workspace[0] / "sms-v1.avsc"
        schema = json.loads(contract.read_text(encoding="utf-8"))
        schema["doc"] = schema["doc"] + " (edited)"
        contract.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        assert self._run(workspace, "--check") == 1

    def test_regeneration_after_a_change_makes_check_pass_again(
        self, workspace: tuple[Path, Path]
    ) -> None:
        self._run(workspace)
        contract = workspace[0] / "sms-v1.avsc"
        schema = json.loads(contract.read_text(encoding="utf-8"))
        schema["doc"] = schema["doc"] + " (edited)"
        contract.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        assert self._run(workspace) == 0
        assert self._run(workspace, "--check") == 0

    def test_write_is_idempotent(
        self, workspace: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._run(workspace)
        capsys.readouterr()
        self._run(workspace)
        assert "0 written" in capsys.readouterr().out

    def test_removed_contract_leaves_a_stale_file_that_is_cleaned(
        self, workspace: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._run(workspace)
        (workspace[0] / "ussd-v1.avsc").unlink()
        assert self._run(workspace, "--check") == 1
        assert "stale" in capsys.readouterr().err
        assert self._run(workspace) == 0
        assert not (workspace[1] / "ussd_v1.py").exists()
        assert self._run(workspace, "--check") == 0

    def test_hand_written_file_in_the_output_directory_is_never_deleted(
        self, workspace: tuple[Path, Path]
    ) -> None:
        self._run(workspace)
        keep = workspace[1] / "notes.py"
        keep.write_text("# written by a human\n", encoding="utf-8")
        assert self._run(workspace) == 0
        assert keep.exists()

    def test_invalid_contract_exits_with_code_2(
        self, workspace: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        contract = workspace[0] / "bundle-v1.avsc"
        schema = json.loads(contract.read_text(encoding="utf-8"))
        schema["fields"][-1]["telecomx.constraint"] = "frobnicate"
        contract.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        assert self._run(workspace) == 2
        assert "invalid contract" in capsys.readouterr().err

    def test_empty_schema_directory_exits_with_code_2(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()
        assert (
            generate.main(
                ["--schema-dir", str(tmp_path / "empty"), "--out-dir", str(tmp_path / "o")]
            )
            == 2
        )
