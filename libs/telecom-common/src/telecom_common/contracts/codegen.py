"""Render Python source and a JSON manifest from parsed contracts.

Everything here is a **pure function** of its input: no clock, no file access, no
randomness. That determinism is what makes the generated files checkable. The CI
gate regenerates in memory and compares with what is committed, so a contract
changed without regenerating (or a generated file edited by hand) fails the build
instead of silently diverging.

What is generated, and what is deliberately not
-----------------------------------------------
Generated files only *declare* things: field types, numeric bounds, patterns, which
fields form a sum rule. The logic that acts on those declarations lives in
:mod:`telecom_common.contracts.base`, hand-written and tested. The rule is simple:
code that nobody may edit must not contain behaviour worth reading.

Rules that need outside state (``unique``, ``in_ref``, ``lte_ref``) cannot be
checked on one record. They are not rendered into models; they travel in the JSON
manifest, which the Spark quality job and the dbt test generator consume.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping, Sequence
from typing import Final

from telecom_common.contracts.constraints import ConstraintKind
from telecom_common.contracts.spec import ContractError, ContractSpec, FieldSpec, FieldType

__all__ = [
    "GENERATED_MARKER",
    "SHARED_ENUMS",
    "is_generated",
    "module_name",
    "render_manifest",
    "render_module",
    "render_registry",
    "validate_specs",
]

GENERATED_MARKER: Final = "# GENERATED FILE - DO NOT EDIT."

#: Avro enums that mirror a hand-maintained domain enum. Instead of generating a
#: second ``Technology`` that could drift from ``telecom_common.domain.Technology``,
#: the generated models import the domain one -- after checking that the symbols
#: of the contract and of the domain enum are *identical*. If someone edits one
#: and forgets the other, generation fails.
SHARED_ENUMS: Final[Mapping[str, tuple[str, str]]] = {
    "Technology": ("telecom_common.domain.enums", "Technology"),
    "SessionTechnology": ("telecom_common.domain.enums", "Technology"),
}

_PYTHON_TYPES: Final[dict[FieldType, str]] = {
    FieldType.STRING: "str",
    FieldType.INT: "int",
    FieldType.LONG: "int",
    FieldType.FLOAT: "float",
    FieldType.DOUBLE: "float",
    FieldType.BOOLEAN: "bool",
    FieldType.TIMESTAMP: "UtcDatetime",
    FieldType.DECIMAL: "Decimal",
}


def module_name(contract_id: str) -> str:
    """Python module name for a contract, e.g. ``cdr-v1`` -> ``cdr_v1``."""
    return contract_id.replace("-", "_")


def is_generated(text: str) -> bool:
    """Whether ``text`` is the content of a file produced by this module."""
    return text.startswith(GENERATED_MARKER)


def validate_specs(specs: Sequence[ContractSpec]) -> None:
    """Check cross-contract uniqueness that single-contract parsing cannot see.

    Raises:
        ContractError: on a duplicated contract id, topic or record name. Two
            contracts on one topic would make the registry ambiguous.
    """
    for label, values in (
        ("contract id", [s.contract_id for s in specs]),
        ("topic", [s.topic for s in specs]),
        ("record name", [s.name for s in specs]),
    ):
        duplicates = sorted({v for v in values if values.count(v) > 1})
        if duplicates:
            msg = f"duplicate {label}: {duplicates}"
            raise ContractError(msg)


def _shared_enum(enum_name: str, symbols: tuple[str, ...]) -> tuple[str, str] | None:
    """Resolve an Avro enum to a shared domain enum, verifying they agree."""
    target = SHARED_ENUMS.get(enum_name)
    if target is None:
        return None
    module_path, class_name = target
    domain_enum = getattr(importlib.import_module(module_path), class_name)
    domain_symbols = tuple(member.value for member in domain_enum)
    if set(domain_symbols) != set(symbols):
        msg = (
            f"enum {enum_name!r} is declared shared with {module_path}.{class_name} but their "
            f"symbols differ: contract has {sorted(symbols)}, domain has {sorted(domain_symbols)}"
        )
        raise ContractError(msg)
    return target


def _enum_class(fld: FieldSpec) -> str:
    """Class name under which the generated module refers to ``fld``'s enum."""
    assert fld.enum_name is not None  # noqa: S101 - guaranteed by FieldType.ENUM
    shared = _shared_enum(fld.enum_name, fld.enum_symbols)
    return shared[1] if shared is not None else fld.enum_name


def _bound_kwargs(fld: FieldSpec) -> list[str]:
    """Pydantic ``Field`` keyword arguments for the numeric and pattern constraints."""
    kwargs: list[str] = []
    names = {
        ConstraintKind.GTE: "ge",
        ConstraintKind.GT: "gt",
        ConstraintKind.LTE: "le",
        ConstraintKind.LT: "lt",
    }
    for kind, kwarg in names.items():
        constraint = fld.constraint(kind)
        if constraint is not None:
            kwargs.append(f"{kwarg}={constraint.value!r}")
    pattern = fld.constraint(ConstraintKind.PATTERN)
    if pattern is not None:
        kwargs.append(f"pattern={pattern.value!r}")
    if fld.type is FieldType.DECIMAL:
        kwargs.append(f"max_digits={fld.precision}")
        kwargs.append(f"decimal_places={fld.scale}")
    return kwargs


def _annotation(fld: FieldSpec) -> str:
    """The type annotation for ``fld``, with bounds and validators attached."""
    base = _enum_class(fld) if fld.type is FieldType.ENUM else _PYTHON_TYPES[fld.type]

    metadata: list[str] = []
    bound_kwargs = _bound_kwargs(fld)
    if bound_kwargs:
        metadata.append(f"Field({', '.join(bound_kwargs)})")
    if fld.constraint(ConstraintKind.NOT_FUTURE) is not None:
        metadata.append("AfterValidator(not_in_future)")

    annotated = f"Annotated[{base}, {', '.join(metadata)}]" if metadata else base
    return f"{annotated} | None" if fld.nullable else annotated


def _default_literal(fld: FieldSpec) -> str:
    """Python source for the field's default value."""
    if fld.default is None:
        return "None"
    if fld.type is FieldType.ENUM:
        return f"{_enum_class(fld)}.{fld.default}"
    return repr(fld.default)


def _field_assignment(fld: FieldSpec) -> str:
    args: list[str] = []
    if fld.has_default:
        args.append(f"default={_default_literal(fld)}")
    args.append(f"description={fld.doc!r}")

    extra: dict[str, str] = {}
    if fld.unit is not None:
        extra["unit"] = fld.unit
    if fld.pii.requires_tokenization:
        extra["pii"] = fld.pii.value
    if extra:
        args.append(f"json_schema_extra={extra!r}")

    return f"    {fld.name}: {_annotation(fld)} = Field({', '.join(args)})"


def _docstring(text: str) -> str:
    """Make ``text`` safe inside a triple-quoted docstring.

    Contract documentation is free text. A backslash or a ``\"\"\"`` in it would
    otherwise yield generated code that is not valid Python.
    """
    escaped = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return escaped + " " if escaped.endswith('"') else escaped


def _render_enum(fld: FieldSpec, contract_id: str) -> str:
    lines = [
        f"class {fld.enum_name}(StrEnum):",
        f'    """Avro enum ``{fld.enum_name}`` of contract ``{contract_id}``."""',
        "",
    ]
    lines.extend(f"    {symbol} = {symbol!r}" for symbol in fld.enum_symbols)
    return "\n".join(lines)


def render_module(spec: ContractSpec, *, source: str) -> str:
    """Render the Python module holding the model of one contract.

    Args:
        spec: the parsed contract.
        source: repo-relative path of the ``.avsc`` it was generated from, for
            the header comment.

    Returns:
        The complete file content, ending with a newline.
    """
    local_enums: dict[str, FieldSpec] = {}
    shared_imports: dict[str, set[str]] = {}
    for fld in spec.fields:
        if fld.type is not FieldType.ENUM:
            continue
        assert fld.enum_name is not None  # noqa: S101 - guaranteed by FieldType.ENUM
        shared = _shared_enum(fld.enum_name, fld.enum_symbols)
        if shared is not None:
            shared_imports.setdefault(shared[0], set()).add(shared[1])
        else:
            if fld.enum_name in local_enums:
                msg = f"{spec.contract_id}: enum {fld.enum_name!r} is declared twice"
                raise ContractError(msg)
            local_enums[fld.enum_name] = fld

    if spec.name in local_enums:
        msg = f"{spec.contract_id}: record and enum share the name {spec.name!r}"
        raise ContractError(msg)

    uses_decimal = any(f.type is FieldType.DECIMAL for f in spec.fields)
    uses_not_future = any(f.constraint(ConstraintKind.NOT_FUTURE) for f in spec.fields)
    uses_annotated = any(
        _bound_kwargs(f) or f.constraint(ConstraintKind.NOT_FUTURE) for f in spec.fields
    )

    lines: list[str] = [
        GENERATED_MARKER,
        f"# Source: {source}",
        "# Regenerate with: make codegen   (python data-platform/schemas/generate.py)",
        "# Editing this file by hand is pointless: CI regenerates it and fails on any difference.",
        "",
        "from __future__ import annotations",
        "",
    ]
    if uses_decimal:
        lines.append("from decimal import Decimal")
    if local_enums:
        lines.append("from enum import StrEnum")
    lines.append(f"from typing import {'Annotated, ' if uses_annotated else ''}ClassVar")
    lines.append("")
    pydantic_imports = (["AfterValidator"] if uses_not_future else []) + ["Field"]
    lines.append(f"from pydantic import {', '.join(pydantic_imports)}")
    lines.append("")
    base_imports = ["ContractModel", "UtcDatetime"] + (["not_in_future"] if uses_not_future else [])
    lines.append(f"from telecom_common.contracts.base import {', '.join(sorted(base_imports))}")
    for module_path in sorted(shared_imports):
        names = ", ".join(sorted(shared_imports[module_path]))
        lines.append(f"from {module_path} import {names}")
    lines.extend(
        ["", f"__all__ = [{', '.join(repr(n) for n in [spec.name, *sorted(local_enums)])}]"]
    )

    for fld in local_enums.values():
        lines.extend(["", "", _render_enum(fld, spec.contract_id)])

    lines.extend(
        ["", "", f"class {spec.name}(ContractModel):", f'    """{_docstring(spec.doc)}"""', ""]
    )
    lines.append(f"    __contract__: ClassVar[str] = {spec.contract_id!r}")
    lines.append(f"    __topic__: ClassVar[str] = {spec.topic!r}")
    lines.append(f"    __partition_key__: ClassVar[str] = {spec.partition_key!r}")
    if spec.sum_rules:
        lines.append(
            "    __sum_rules__: ClassVar[tuple[tuple[str, tuple[str, ...]], ...]] = "
            f"{spec.sum_rules!r}"
        )
    lines.append("")
    lines.extend(_field_assignment(fld) for fld in spec.fields)

    return "\n".join(lines) + "\n"


def render_registry(specs: Sequence[ContractSpec]) -> str:
    """Render ``generated/__init__.py``: every model, indexed by contract and topic."""
    ordered = sorted(specs, key=lambda s: s.contract_id)
    lines = [
        GENERATED_MARKER,
        "# Regenerate with: make codegen   (python data-platform/schemas/generate.py)",
        "",
        "from __future__ import annotations",
        "",
        "from typing import Final",
        "",
        "from telecom_common.contracts.base import ContractModel",
    ]
    for spec in ordered:
        lines.append(
            f"from telecom_common.contracts.generated.{module_name(spec.contract_id)} "
            f"import {spec.name}"
        )
    lines.extend(
        [
            "",
            "#: Every event model, keyed by contract id (the .avsc file stem).",
            "MODELS: Final[dict[str, type[ContractModel]]] = {",
        ]
    )
    lines.extend(f"    {spec.contract_id!r}: {spec.name}," for spec in ordered)
    lines.extend(
        [
            "}",
            "",
            "#: The same models, keyed by Kafka topic. A topic maps to exactly one contract.",
            "BY_TOPIC: Final[dict[str, type[ContractModel]]] = {",
        ]
    )
    lines.extend(f"    {spec.topic!r}: {spec.name}," for spec in ordered)
    lines.extend(
        ["}", "", f"__all__ = ['BY_TOPIC', 'MODELS', {', '.join(repr(s.name) for s in ordered)}]"]
    )
    return "\n".join(lines) + "\n"


def render_manifest(specs: Sequence[ContractSpec]) -> str:
    """Render the language-neutral JSON manifest of every contract.

    This is what Spark and dbt read. It carries, for each field, the unit, the PII
    class, the token kind, and **all** constraints including those a single record
    cannot check (``unique``, ``in_ref``, ``lte_ref``).
    """
    payload = {
        "manifest_version": 1,
        "contracts": {
            s.contract_id: s.to_dict() for s in sorted(specs, key=lambda s: s.contract_id)
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
