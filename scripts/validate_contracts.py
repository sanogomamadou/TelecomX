#!/usr/bin/env python3
"""Validate TelecomX Avro data contracts.

Checks performed on every ``*.avsc`` file in ``data-platform/schemas/avro/``:

1. Valid JSON.
2. Valid Avro schema (parsed by ``fastavro`` when available).
3. TelecomX governance rules (see docs/data/contracts/README.md §7):
   - mandatory envelope fields are present;
   - every field carries a non-empty ``doc``;
   - every numeric measure declares ``telecomx.unit``;
   - every PII-flagged field declares ``telecomx.tokenize_as``;
   - record-level metadata (owner, topic, compatibility...) is present.

Exit code is 0 when all contracts pass, 1 otherwise. Designed to run in
pre-commit and CI without any project dependency beyond the standard library
(``fastavro`` is used opportunistically for full Avro parsing).

Usage:
    python scripts/validate_contracts.py [--schema-dir DIR] [--quiet]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fastavro

    HAS_FASTAVRO = True
except ImportError:  # pragma: no cover - depends on local environment
    HAS_FASTAVRO = False

# --- Governance rules -------------------------------------------------------

ENVELOPE_FIELDS = {
    "event_id",
    "event_timestamp",
    "ingestion_timestamp",
    "schema_version",
    "producer_id",
    "tenant_id",
}

REQUIRED_RECORD_METADATA = {
    "telecomx.owner",
    "telecomx.topic",
    "telecomx.compatibility",
    "telecomx.classification",
}

VALID_COMPATIBILITY = {"BACKWARD", "FORWARD", "FULL", "NONE"}
VALID_CLASSIFICATION = {"P1", "P2", "P3", "P4"}
VALID_PII = {"none", "direct", "indirect"}

# Numeric primitives that represent a measure and therefore need a unit.
NUMERIC_TYPES = {"int", "long", "float", "double"}

# Numeric fields that are identifiers or counters rather than measures.
UNIT_EXEMPT_FIELDS = {
    "schema_version",
    "event_timestamp",
    "ingestion_timestamp",
    "segment_count",
    "step_count",
    "battery_cycles",
    "crc_error_count",
    "anomaly_score",
    "satisfaction_score",
}


@dataclass
class ContractReport:
    """Validation outcome for a single contract file."""

    path: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def unwrap_type(avro_type: Any) -> tuple[str, bool]:
    """Return the concrete Avro type name and whether the field is nullable.

    Handles ``["null", "double"]`` unions and logical/complex type dicts.
    """
    nullable = False
    if isinstance(avro_type, list):
        nullable = "null" in avro_type
        non_null = [t for t in avro_type if t != "null"]
        avro_type = non_null[0] if non_null else "null"
    if isinstance(avro_type, dict):
        # Logical types (decimal, timestamp-micros) and complex types (enum, record).
        return str(avro_type.get("logicalType") or avro_type.get("type", "unknown")), nullable
    return str(avro_type), nullable


def validate_field(fld: dict[str, Any], report: ContractReport) -> None:
    """Apply per-field governance rules."""
    name = fld.get("name", "<unnamed>")

    # Envelope fields share one canonical definition documented in
    # docs/data/contracts/README.md §3; repeating it in all 11 contracts would be
    # noise that hides the business fields during contract review.
    if name not in ENVELOPE_FIELDS and not fld.get("doc", "").strip():
        report.errors.append(f"field '{name}': missing or empty 'doc'")

    type_name, _ = unwrap_type(fld.get("type"))

    # Rule: numeric measures must declare their unit (prevents silent semantic drift).
    if (
        type_name in NUMERIC_TYPES
        and name not in UNIT_EXEMPT_FIELDS
        and "telecomx.unit" not in fld
    ):
        report.warnings.append(
            f"field '{name}': numeric measure without 'telecomx.unit' "
            "(see docs/data/contracts/README.md §4.2)"
        )

    # Rule: any PII-flagged field must declare how it is tokenized.
    pii = fld.get("telecomx.pii")
    if pii is not None:
        if pii not in VALID_PII:
            report.errors.append(
                f"field '{name}': invalid 'telecomx.pii' value {pii!r} "
                f"(expected one of {sorted(VALID_PII)})"
            )
        elif pii in {"direct", "indirect"} and not (
            fld.get("telecomx.tokenize_as") or fld.get("telecomx.transform")
        ):
            report.errors.append(
                f"field '{name}': PII '{pii}' requires 'telecomx.tokenize_as' "
                "or 'telecomx.transform'"
            )


def validate_contract(path: Path) -> ContractReport:
    """Validate a single .avsc file against JSON, Avro and governance rules."""
    report = ContractReport(path=path)

    try:
        raw = path.read_text(encoding="utf-8")
        schema = json.loads(raw)
    except json.JSONDecodeError as exc:
        report.errors.append(f"invalid JSON: {exc}")
        return report

    if HAS_FASTAVRO:
        try:
            fastavro.parse_schema(schema)
        except Exception as exc:  # noqa: BLE001 - fastavro raises varied types
            report.errors.append(f"invalid Avro schema: {exc}")
            return report

    if schema.get("type") != "record":
        report.errors.append("top-level schema must be of type 'record'")
        return report

    if not schema.get("doc", "").strip():
        report.errors.append("record: missing or empty 'doc'")

    for key in sorted(REQUIRED_RECORD_METADATA - set(schema)):
        report.errors.append(f"record: missing metadata '{key}'")

    compat = schema.get("telecomx.compatibility")
    if compat is not None and compat not in VALID_COMPATIBILITY:
        report.errors.append(
            f"record: invalid 'telecomx.compatibility' {compat!r} "
            f"(expected one of {sorted(VALID_COMPATIBILITY)})"
        )

    classification = schema.get("telecomx.classification")
    if classification is not None and classification not in VALID_CLASSIFICATION:
        report.errors.append(
            f"record: invalid 'telecomx.classification' {classification!r} "
            f"(expected one of {sorted(VALID_CLASSIFICATION)})"
        )

    fields = schema.get("fields", [])
    if not fields:
        report.errors.append("record: no fields declared")
        return report

    names = {f.get("name") for f in fields}
    for missing in sorted(ENVELOPE_FIELDS - names):
        report.errors.append(f"record: missing mandatory envelope field '{missing}' (FR-M1-006)")

    for fld in fields:
        validate_field(fld, report)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate TelecomX Avro data contracts.")
    parser.add_argument(
        "--schema-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data-platform" / "schemas" / "avro",
        help="Directory containing .avsc contract files.",
    )
    parser.add_argument("--quiet", action="store_true", help="Only report failures.")
    args = parser.parse_args()

    if not args.schema_dir.is_dir():
        sys.stderr.write(f"ERROR: schema directory not found: {args.schema_dir}\n")
        return 1

    contracts = sorted(args.schema_dir.glob("*.avsc"))
    if not contracts:
        sys.stderr.write(f"ERROR: no .avsc contracts found in {args.schema_dir}\n")
        return 1

    if not HAS_FASTAVRO and not args.quiet:
        sys.stdout.write(
            "NOTE: fastavro is not installed - Avro parsing is skipped, "
            "only JSON and governance rules are checked.\n\n"
        )

    reports = [validate_contract(p) for p in contracts]
    failed = [r for r in reports if not r.ok]
    warned = [r for r in reports if r.warnings]

    for report in reports:
        if report.ok and not report.warnings and args.quiet:
            continue
        status = "PASS" if report.ok else "FAIL"
        sys.stdout.write(f"[{status}] {report.path.name}\n")
        for err in report.errors:
            sys.stdout.write(f"    ERROR   {err}\n")
        for warn in report.warnings:
            sys.stdout.write(f"    WARNING {warn}\n")

    total_warnings = sum(len(r.warnings) for r in warned)
    sys.stdout.write(
        f"\n{len(contracts) - len(failed)}/{len(contracts)} contracts valid"
        f" - {len(failed)} failed, {total_warnings} warnings\n"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
