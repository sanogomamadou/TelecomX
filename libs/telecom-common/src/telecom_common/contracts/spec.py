"""In-memory model of a data contract, parsed from an Avro ``.avsc`` file.

The ``.avsc`` files in ``data-platform/schemas/avro/`` are the single source of
truth (ADR-015). Everything else -- Pydantic models, the JSON manifest consumed by
Spark and dbt -- is *derived* from the :class:`ContractSpec` built here, so a
divergence between them is structurally impossible rather than merely discouraged.

This module is the one place where a contract is checked for structural
soundness. A contract that parses is one the generator can safely turn into code;
one that does not fails here with a message naming the contract and the field,
instead of producing subtly wrong generated code.

Scope: it supports the subset of Avro the platform actually uses (primitives,
``timestamp-micros``, ``decimal``, inline enums, and ``["null", T]`` unions).
Anything else raises :class:`ContractError` rather than being guessed at.
"""

from __future__ import annotations

import keyword
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from telecom_common.contracts.constraints import (
    Constraint,
    ConstraintKind,
    ConstraintSyntaxError,
    parse_constraints,
)
from telecom_common.domain.enums import PiiClass

__all__ = [
    "ENVELOPE_DOCS",
    "ENVELOPE_FIELDS",
    "TOKEN_TARGETS",
    "ContractError",
    "ContractSpec",
    "FieldSpec",
    "FieldType",
    "parse_avsc",
]

#: Fields every contract must carry (FR-M1-006). Their meaning is identical in
#: all contracts, so it is documented once here instead of being repeated
#: eleven times in the ``.avsc`` files, where it would only bury the business
#: fields during contract review.
ENVELOPE_DOCS: Final[dict[str, str]] = {
    "event_id": "UUID v7, unique per event. Deduplication key.",
    "event_timestamp": "Instant the event occurred in the real world, in UTC.",
    "ingestion_timestamp": "Instant the event entered the platform, in UTC. Drives freshness.",
    "schema_version": "Semantic version of the contract, carried by the event for debugging.",
    "producer_id": "Identifier of the emitting system or adapter.",
    "tenant_id": "Tenant owning the event. A message without it is routed to the DLQ (ADR-016).",
}
ENVELOPE_FIELDS: Final = frozenset(ENVELOPE_DOCS)

#: Token kinds a PII field may be converted into. A closed set, so that a typo in
#: ``telecomx.tokenize_as`` fails the build instead of creating a new, unreviewed
#: kind of token.
TOKEN_TARGETS: Final = frozenset(
    {
        "subscriber_token",
        "callee_token",
        "receiver_token",
        "sender_token",
        "imsi_token",
        "device_token",
        "account_token",
    }
)

_VALID_COMPATIBILITY: Final = frozenset({"BACKWARD", "FORWARD", "FULL", "NONE"})
_VALID_CLASSIFICATION: Final = frozenset({"P1", "P2", "P3", "P4"})


class ContractError(ValueError):
    """Raised when a contract is structurally unsound or uses unsupported Avro."""


class FieldType(StrEnum):
    """Logical type of a field, after Avro unions and logical types are resolved."""

    STRING = "string"
    INT = "int"
    LONG = "long"
    FLOAT = "float"
    DOUBLE = "double"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp-micros"
    DECIMAL = "decimal"
    ENUM = "enum"

    @property
    def is_numeric(self) -> bool:
        """Whether ordering constraints (``gte``, ``lt``...) apply."""
        return self in {
            FieldType.INT,
            FieldType.LONG,
            FieldType.FLOAT,
            FieldType.DOUBLE,
            FieldType.DECIMAL,
        }


_PRIMITIVES: Final[dict[str, FieldType]] = {
    "string": FieldType.STRING,
    "int": FieldType.INT,
    "long": FieldType.LONG,
    "float": FieldType.FLOAT,
    "double": FieldType.DOUBLE,
    "boolean": FieldType.BOOLEAN,
}


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One field of a contract, with its governance metadata."""

    name: str
    type: FieldType
    nullable: bool
    doc: str
    has_default: bool
    default: Any
    enum_name: str | None
    enum_symbols: tuple[str, ...]
    precision: int | None
    scale: int | None
    unit: str | None
    pii: PiiClass
    tokenize_as: str | None
    transform: str | None
    constraints: tuple[Constraint, ...]

    @property
    def is_envelope(self) -> bool:
        """Whether this is one of the mandatory envelope fields."""
        return self.name in ENVELOPE_FIELDS

    @property
    def is_required(self) -> bool:
        """Whether a producer must supply a value (no default declared)."""
        return not self.has_default

    def constraint(self, kind: ConstraintKind) -> Constraint | None:
        """Return the constraint of ``kind`` on this field, if declared."""
        return next((c for c in self.constraints if c.kind is kind), None)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly mapping (used by the manifest)."""
        return {
            "name": self.name,
            "type": self.type.value,
            "nullable": self.nullable,
            "doc": self.doc,
            "has_default": self.has_default,
            "default": self.default,
            "enum_name": self.enum_name,
            "enum_symbols": list(self.enum_symbols),
            "precision": self.precision,
            "scale": self.scale,
            "unit": self.unit,
            "pii": self.pii.value,
            "tokenize_as": self.tokenize_as,
            "transform": self.transform,
            "constraints": [c.to_dict() for c in self.constraints],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FieldSpec:
        """Inverse of :meth:`to_dict`."""
        return cls(
            name=data["name"],
            type=FieldType(data["type"]),
            nullable=data["nullable"],
            doc=data["doc"],
            has_default=data["has_default"],
            default=data["default"],
            enum_name=data["enum_name"],
            enum_symbols=tuple(data["enum_symbols"]),
            precision=data["precision"],
            scale=data["scale"],
            unit=data["unit"],
            pii=PiiClass(data["pii"]),
            tokenize_as=data["tokenize_as"],
            transform=data["transform"],
            constraints=tuple(Constraint.from_dict(c) for c in data["constraints"]),
        )


@dataclass(frozen=True, slots=True)
class ContractSpec:
    """A whole contract: record-level metadata plus its fields."""

    contract_id: str
    name: str
    namespace: str
    doc: str
    owner: str
    topic: str
    partition_key: str
    freshness_seconds: int
    compatibility: str
    classification: str
    fields: tuple[FieldSpec, ...]

    def field(self, name: str) -> FieldSpec:
        """Return the field called ``name``.

        Raises:
            KeyError: if the contract has no such field.
        """
        for candidate in self.fields:
            if candidate.name == name:
                return candidate
        raise KeyError(name)

    @property
    def pii_fields(self) -> tuple[FieldSpec, ...]:
        """Fields that must never reach silver in the clear (ADR-013)."""
        return tuple(f for f in self.fields if f.pii.requires_tokenization)

    @property
    def sum_rules(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """``(total_field, (part, part, ...))`` pairs from ``eq_sum`` constraints."""
        rules: list[tuple[str, tuple[str, ...]]] = []
        for fld in self.fields:
            eq_sum = fld.constraint(ConstraintKind.EQ_SUM)
            if eq_sum is not None and isinstance(eq_sum.value, tuple):
                rules.append((fld.name, eq_sum.value))
        return tuple(rules)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly mapping (used by the manifest)."""
        return {
            "name": self.name,
            "namespace": self.namespace,
            "doc": self.doc,
            "owner": self.owner,
            "topic": self.topic,
            "partition_key": self.partition_key,
            "freshness_seconds": self.freshness_seconds,
            "compatibility": self.compatibility,
            "classification": self.classification,
            "fields": [f.to_dict() for f in self.fields],
        }

    @classmethod
    def from_dict(cls, contract_id: str, data: dict[str, Any]) -> ContractSpec:
        """Inverse of :meth:`to_dict`."""
        return cls(
            contract_id=contract_id,
            name=data["name"],
            namespace=data["namespace"],
            doc=data["doc"],
            owner=data["owner"],
            topic=data["topic"],
            partition_key=data["partition_key"],
            freshness_seconds=data["freshness_seconds"],
            compatibility=data["compatibility"],
            classification=data["classification"],
            fields=tuple(FieldSpec.from_dict(f) for f in data["fields"]),
        )


# --- Parsing ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ResolvedType:
    type: FieldType
    nullable: bool
    enum_name: str | None = None
    enum_symbols: tuple[str, ...] = ()
    precision: int | None = None
    scale: int | None = None


def _resolve_type(where: str, raw: object) -> _ResolvedType:
    """Collapse an Avro type expression into a :class:`_ResolvedType`."""
    if isinstance(raw, list):
        branches = [b for b in raw if b != "null"]
        if len(branches) != 1 or "null" not in raw or len(raw) != 2:
            msg = f"{where}: only ['null', T] unions are supported, got {raw!r}"
            raise ContractError(msg)
        inner = _resolve_type(where, branches[0])
        return _ResolvedType(
            type=inner.type,
            nullable=True,
            enum_name=inner.enum_name,
            enum_symbols=inner.enum_symbols,
            precision=inner.precision,
            scale=inner.scale,
        )

    if isinstance(raw, str):
        if raw in _PRIMITIVES:
            return _ResolvedType(type=_PRIMITIVES[raw], nullable=False)
        msg = f"{where}: unsupported Avro type {raw!r}"
        raise ContractError(msg)

    if isinstance(raw, dict):
        avro_type = raw.get("type")
        logical = raw.get("logicalType")

        if avro_type == "enum":
            name = raw.get("name")
            symbols = raw.get("symbols")
            if not isinstance(name, str) or not isinstance(symbols, list) or not symbols:
                msg = f"{where}: enum needs a name and a non-empty symbol list"
                raise ContractError(msg)
            for symbol in symbols:
                if (
                    not isinstance(symbol, str)
                    or not symbol.isidentifier()
                    or keyword.iskeyword(symbol)
                ):
                    msg = f"{where}: enum symbol {symbol!r} is not a usable Python identifier"
                    raise ContractError(msg)
            return _ResolvedType(
                type=FieldType.ENUM,
                nullable=False,
                enum_name=name,
                enum_symbols=tuple(symbols),
            )

        if avro_type == "long" and logical == "timestamp-micros":
            return _ResolvedType(type=FieldType.TIMESTAMP, nullable=False)

        if avro_type == "bytes" and logical == "decimal":
            precision, scale = raw.get("precision"), raw.get("scale", 0)
            if not isinstance(precision, int) or not isinstance(scale, int):
                msg = f"{where}: decimal needs integer precision and scale"
                raise ContractError(msg)
            return _ResolvedType(
                type=FieldType.DECIMAL, nullable=False, precision=precision, scale=scale
            )

        if isinstance(avro_type, str) and avro_type in _PRIMITIVES and logical is None:
            return _ResolvedType(type=_PRIMITIVES[avro_type], nullable=False)

    msg = f"{where}: unsupported Avro type expression {raw!r}"
    raise ContractError(msg)


def _check_constraints(where: str, spec: FieldSpec) -> None:
    """Reject constraints that make no sense on the field they decorate."""
    for constraint in spec.constraints:
        kind = constraint.kind
        if (
            kind
            in {
                ConstraintKind.GTE,
                ConstraintKind.GT,
                ConstraintKind.LTE,
                ConstraintKind.LT,
                ConstraintKind.EQ_SUM,
                ConstraintKind.LTE_REF,
            }
            and not spec.type.is_numeric
        ):
            msg = f"{where}: constraint '{kind.value}' needs a numeric field, got {spec.type.value}"
            raise ContractError(msg)
        if kind is ConstraintKind.PATTERN and spec.type is not FieldType.STRING:
            msg = f"{where}: constraint 'pattern' needs a string field, got {spec.type.value}"
            raise ContractError(msg)
        if kind is ConstraintKind.NOT_FUTURE and spec.type is not FieldType.TIMESTAMP:
            msg = f"{where}: constraint 'not_future' needs a timestamp field, got {spec.type.value}"
            raise ContractError(msg)
        if kind is ConstraintKind.NOT_NULL and spec.nullable:
            msg = f"{where}: 'not_null' contradicts the nullable Avro type"
            raise ContractError(msg)

    def bound(*kinds: ConstraintKind) -> tuple[float, bool] | None:
        for kind in kinds:
            found = spec.constraint(kind)
            if found is not None and isinstance(found.value, (int, float)):
                return float(found.value), kind in {ConstraintKind.GT, ConstraintKind.LT}
        return None

    lower = bound(ConstraintKind.GTE, ConstraintKind.GT)
    upper = bound(ConstraintKind.LTE, ConstraintKind.LT)
    if lower is not None and upper is not None:
        (low, low_strict), (high, high_strict) = lower, upper
        if low > high or (low == high and (low_strict or high_strict)):
            msg = f"{where}: lower bound {low} is incompatible with upper bound {high}"
            raise ContractError(msg)


def _parse_field(contract_id: str, raw: dict[str, Any]) -> FieldSpec:
    name = raw.get("name")
    if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
        msg = f"{contract_id}: invalid field name {name!r}"
        raise ContractError(msg)
    where = f"{contract_id}.{name}"

    resolved = _resolve_type(where, raw.get("type"))

    doc = str(raw.get("doc", "")).strip()
    if not doc:
        if name in ENVELOPE_FIELDS:
            doc = ENVELOPE_DOCS[name]
        else:
            msg = f"{where}: missing 'doc' (every business field must be documented)"
            raise ContractError(msg)

    has_default = "default" in raw
    default = raw.get("default")
    if has_default:
        if resolved.nullable and default is not None:
            msg = f"{where}: a nullable field's default must be null, got {default!r}"
            raise ContractError(msg)
        if (
            resolved.type is FieldType.ENUM
            and default is not None
            and default not in (resolved.enum_symbols)
        ):
            msg = f"{where}: default {default!r} is not one of {list(resolved.enum_symbols)}"
            raise ContractError(msg)
    if resolved.nullable and not has_default:
        msg = f"{where}: a nullable field must declare 'default': null"
        raise ContractError(msg)

    try:
        pii = PiiClass(raw.get("telecomx.pii", "none"))
    except ValueError:
        msg = f"{where}: invalid telecomx.pii {raw.get('telecomx.pii')!r}"
        raise ContractError(msg) from None

    tokenize_as = raw.get("telecomx.tokenize_as")
    transform = raw.get("telecomx.transform")
    if pii.requires_tokenization and not (tokenize_as or transform):
        msg = f"{where}: PII '{pii.value}' requires telecomx.tokenize_as or telecomx.transform"
        raise ContractError(msg)
    if tokenize_as is not None and tokenize_as not in TOKEN_TARGETS:
        msg = f"{where}: unknown token kind {tokenize_as!r}; known: {sorted(TOKEN_TARGETS)}"
        raise ContractError(msg)

    try:
        constraints = parse_constraints(raw.get("telecomx.constraint"))
    except ConstraintSyntaxError as exc:
        msg = f"{where}: {exc}"
        raise ContractError(msg) from exc

    spec = FieldSpec(
        name=name,
        type=resolved.type,
        nullable=resolved.nullable,
        doc=doc,
        has_default=has_default,
        default=default,
        enum_name=resolved.enum_name,
        enum_symbols=resolved.enum_symbols,
        precision=resolved.precision,
        scale=resolved.scale,
        unit=raw.get("telecomx.unit"),
        pii=pii,
        tokenize_as=tokenize_as,
        transform=transform,
        constraints=constraints,
    )
    _check_constraints(where, spec)
    return spec


def parse_avsc(contract_id: str, schema: dict[str, Any]) -> ContractSpec:
    """Build a :class:`ContractSpec` from a parsed ``.avsc`` document.

    Args:
        contract_id: identifier derived from the file name, e.g. ``cdr-v1``.
        schema: the JSON content of the ``.avsc`` file.

    Raises:
        ContractError: if the contract is structurally unsound. The message
            always names the contract and, where relevant, the field.
    """
    if schema.get("type") != "record":
        msg = f"{contract_id}: top-level schema must be a record"
        raise ContractError(msg)

    def required(key: str) -> Any:
        value = schema.get(key)
        if value in (None, ""):
            msg = f"{contract_id}: missing record metadata {key!r}"
            raise ContractError(msg)
        return value

    compatibility = str(required("telecomx.compatibility"))
    classification = str(required("telecomx.classification"))
    if compatibility not in _VALID_COMPATIBILITY:
        msg = f"{contract_id}: invalid compatibility {compatibility!r}"
        raise ContractError(msg)
    if classification not in _VALID_CLASSIFICATION:
        msg = f"{contract_id}: invalid classification {classification!r}"
        raise ContractError(msg)

    raw_fields = schema.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        msg = f"{contract_id}: no fields declared"
        raise ContractError(msg)

    fields = tuple(_parse_field(contract_id, f) for f in raw_fields)

    names = [f.name for f in fields]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        msg = f"{contract_id}: duplicate field names {duplicates}"
        raise ContractError(msg)

    missing = sorted(ENVELOPE_FIELDS - set(names))
    if missing:
        msg = f"{contract_id}: missing mandatory envelope fields {missing} (FR-M1-006)"
        raise ContractError(msg)

    partition_key = str(required("telecomx.partition_key"))
    if partition_key not in names:
        msg = f"{contract_id}: partition key {partition_key!r} is not a field of the contract"
        raise ContractError(msg)

    by_name = {f.name: f for f in fields}
    for fld in fields:
        eq_sum = fld.constraint(ConstraintKind.EQ_SUM)
        if eq_sum is not None and isinstance(eq_sum.value, tuple):
            for operand in eq_sum.value:
                target = by_name.get(operand)
                if target is None or not target.type.is_numeric:
                    msg = (
                        f"{contract_id}.{fld.name}: eq_sum operand {operand!r} "
                        "is not a numeric field of this contract"
                    )
                    raise ContractError(msg)

    return ContractSpec(
        contract_id=contract_id,
        name=str(required("name")),
        namespace=str(required("namespace")),
        doc=str(required("doc")).strip(),
        owner=str(required("telecomx.owner")),
        topic=str(required("telecomx.topic")),
        partition_key=partition_key,
        freshness_seconds=int(required("telecomx.sla_freshness_seconds")),
        compatibility=compatibility,
        classification=classification,
        fields=fields,
    )
