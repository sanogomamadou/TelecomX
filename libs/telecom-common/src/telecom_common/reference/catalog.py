"""The reference catalogue: what exists in the network, and the rules that depend on it.

A :class:`ReferenceCatalog` holds the ``ref.*`` tables in memory. It does three jobs
that nothing else in the platform can do:

1. **Persist** them as CSV (the dbt seeds, versioned in Git) and read them back,
   validating every row on the way in.
2. **Prove they are coherent**: every cell has a site, every site a region, no key is
   duplicated, enumerated values match the contracts, dates are in order.
3. **Complete the contracts**. Three rules of the contract grammar cannot be checked
   on one record: ``in_ref`` ("this cell exists"), ``lte_ref`` ("active users do not
   exceed the cell's capacity") and, indirectly, tenant scoping. They need reference
   data, and this class is what provides it (:meth:`ReferenceCatalog.check_record`).

Which row does ``lte_ref`` mean?
--------------------------------
``lte_ref:cells.capacity_users`` names a *column* but not a *row*. The convention is
that the row is the one designated by the field of the same record that carries
``in_ref`` on the same table: for ``active_users`` that is ``cell_id``. If a contract
makes that ambiguous (zero or several candidate fields), the check says so instead of
guessing.

Tenant scoping (ADR-016)
------------------------
Tenant-scoped tables are looked up by ``(tenant_id, key)``. A cell that exists for one
tenant does not exist for another, even if the identifier is the same. Looking up a
scoped table without a tenant is an error, not a "not found".
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from telecom_common.contracts.constraints import ConstraintKind
from telecom_common.contracts.generated.bundle_v1 import BundleType
from telecom_common.contracts.generated.equipment_telemetry_v1 import EquipmentType
from telecom_common.contracts.generated.network_alarms_v1 import AlarmDomain, AlarmSeverity
from telecom_common.contracts.spec import ContractSpec
from telecom_common.domain.entities import (
    AlarmTypeRef,
    BundleRef,
    CellRef,
    EquipmentRef,
    RegionRef,
    SiteRef,
    TechnologyRef,
    TenantRef,
)

__all__ = [
    "TABLES",
    "CatalogError",
    "ReferenceCatalog",
]


class CatalogError(ValueError):
    """Raised when reference data cannot be loaded or fails validation."""


@dataclass(frozen=True, slots=True)
class _Table:
    """Static description of one reference table."""

    name: str  # CSV stem and the name used by `in_ref:<name>` in the contracts
    attr: str  # attribute holding the rows on ReferenceCatalog
    model: type[BaseModel]
    key: str  # column designated by an `in_ref`
    tenant_scoped: bool


#: Every reference table, in dump order. Order matters only for readability of diffs.
TABLES: Final[tuple[_Table, ...]] = (
    _Table("tenants", "tenants", TenantRef, "tenant_id", False),
    _Table("technologies", "technologies", TechnologyRef, "technology", False),
    _Table("regions", "regions", RegionRef, "region_code", True),
    _Table("sites", "sites", SiteRef, "site_id", True),
    _Table("cells", "cells", CellRef, "cell_id", True),
    _Table("equipment", "equipment", EquipmentRef, "equipment_id", True),
    _Table("bundles", "bundles", BundleRef, "bundle_code", False),
    _Table("alarm_types", "alarm_types", AlarmTypeRef, "alarm_code", False),
)
_BY_NAME: Final = {t.name: t for t in TABLES}


def _format_cell(value: Any) -> str:
    """Render one value for CSV. Stable, so that regenerating yields identical bytes."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _render_csv(table: _Table, rows: Sequence[BaseModel]) -> str:
    columns = list(table.model.model_fields)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        dumped = row.model_dump(mode="json")
        writer.writerow([_format_cell(dumped[column]) for column in columns])
    return buffer.getvalue()


def _parse_csv(table: _Table, text: str) -> tuple[BaseModel, ...]:
    reader = csv.DictReader(io.StringIO(text, newline=""))
    expected = list(table.model.model_fields)
    if reader.fieldnames != expected:
        msg = f"{table.name}.csv: columns {reader.fieldnames} do not match expected {expected}"
        raise CatalogError(msg)
    rows: list[BaseModel] = []
    for line_number, raw in enumerate(reader, start=2):
        data = {column: (value if value != "" else None) for column, value in raw.items()}
        try:
            rows.append(table.model.model_validate(data))
        except ValidationError as exc:
            msg = f"{table.name}.csv line {line_number}: {exc}"
            raise CatalogError(msg) from exc
    return tuple(rows)


@dataclass(frozen=True)
class ReferenceCatalog:
    """The reference tables of the platform, in memory."""

    tenants: tuple[TenantRef, ...]
    technologies: tuple[TechnologyRef, ...]
    regions: tuple[RegionRef, ...]
    sites: tuple[SiteRef, ...]
    cells: tuple[CellRef, ...]
    equipment: tuple[EquipmentRef, ...]
    bundles: tuple[BundleRef, ...]
    alarm_types: tuple[AlarmTypeRef, ...]

    # -- Persistence ---------------------------------------------------------

    def render_csv(self) -> dict[str, str]:
        """Return ``{file name: content}`` for every table. Pure: writes nothing."""
        return {f"{t.name}.csv": _render_csv(t, getattr(self, t.attr)) for t in TABLES}

    @classmethod
    def from_csv(cls, files: Mapping[str, str]) -> ReferenceCatalog:
        """Build a catalogue from ``{file name: content}``, validating each row.

        Raises:
            CatalogError: on a missing file, a column mismatch, an invalid row, or
                an incoherent catalogue (see :meth:`validate`).
        """
        parsed: dict[str, tuple[BaseModel, ...]] = {}
        for table in TABLES:
            name = f"{table.name}.csv"
            if name not in files:
                msg = f"missing reference file {name}"
                raise CatalogError(msg)
            parsed[table.attr] = _parse_csv(table, files[name])
        catalog = cls(**parsed)  # type: ignore[arg-type]
        problems = catalog.validate()
        if problems:
            shown = "\n  ".join(problems[:20])
            more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
            msg = f"reference catalogue is incoherent ({len(problems)} problems):\n  {shown}{more}"
            raise CatalogError(msg)
        return catalog

    @classmethod
    def load(cls, directory: Path) -> ReferenceCatalog:
        """Read the CSV files of ``directory``."""
        files = {
            f"{t.name}.csv": (directory / f"{t.name}.csv").read_text(encoding="utf-8")
            for t in TABLES
            if (directory / f"{t.name}.csv").is_file()
        }
        return cls.from_csv(files)

    # -- Lookup --------------------------------------------------------------

    @cached_property
    def _indexes(self) -> dict[str, dict[tuple[str | None, str], BaseModel]]:
        indexes: dict[str, dict[tuple[str | None, str], BaseModel]] = {}
        for table in TABLES:
            index: dict[tuple[str | None, str], BaseModel] = {}
            for row in getattr(self, table.attr):
                tenant = row.tenant_id if table.tenant_scoped else None
                index[(tenant, str(getattr(row, table.key)))] = row
            indexes[table.name] = index
        return indexes

    def get(self, table: str, key: str, tenant_id: str | None = None) -> Any:
        """Return the row of ``table`` identified by ``key``, or ``None``.

        Raises:
            KeyError: if ``table`` is unknown.
            ValueError: if ``table`` is tenant-scoped and no tenant was given. That
                is a caller bug, and answering "not found" would hide it.
        """
        spec = _BY_NAME[table]
        if spec.tenant_scoped and tenant_id is None:
            msg = f"table {table!r} is tenant-scoped: a tenant_id is required"
            raise ValueError(msg)
        scope = tenant_id if spec.tenant_scoped else None
        return self._indexes[table].get((scope, str(key)))

    def has(self, table: str, key: str, tenant_id: str | None = None) -> bool:
        """Whether ``key`` exists in ``table`` (for ``tenant_id`` when scoped)."""
        return self.get(table, key, tenant_id) is not None

    def keys(self, table: str, tenant_id: str | None = None) -> frozenset[str]:
        """Every key of ``table``, restricted to ``tenant_id`` when the table is scoped."""
        spec = _BY_NAME[table]
        if spec.tenant_scoped and tenant_id is None:
            msg = f"table {table!r} is tenant-scoped: a tenant_id is required"
            raise ValueError(msg)
        scope = tenant_id if spec.tenant_scoped else None
        return frozenset(key for (tenant, key) in self._indexes[table] if tenant == scope)

    # -- Coherence -----------------------------------------------------------

    def validate(self) -> list[str]:
        """Return every coherence problem found; an empty list means a sound catalogue."""
        problems: list[str] = []

        for table in TABLES:
            seen: set[tuple[str | None, str]] = set()
            for row in getattr(self, table.attr):
                tenant = row.tenant_id if table.tenant_scoped else None
                identity = (tenant, str(getattr(row, table.key)))
                if identity in seen:
                    problems.append(
                        f"{table.name}: duplicate key {identity[1]!r} (tenant {tenant})"
                    )
                seen.add(identity)

        tenant_ids = {t.tenant_id for t in self.tenants}
        technologies = {t.technology for t in self.technologies}
        regions = {(r.tenant_id, r.region_code) for r in self.regions}
        sites = {(s.tenant_id, s.site_id): s for s in self.sites}

        for region in self.regions:
            if region.tenant_id not in tenant_ids:
                problems.append(
                    f"regions: {region.region_code} has unknown tenant {region.tenant_id!r}"
                )

        for site in self.sites:
            if (site.tenant_id, site.region_code) not in regions:
                problems.append(
                    f"sites: {site.site_id} references unknown region {site.region_code!r}"
                )

        cells_per_site: dict[tuple[str, str], int] = {}
        for cell in self.cells:
            parent = (cell.tenant_id, cell.site_id)
            if parent not in sites:
                problems.append(f"cells: {cell.cell_id} references unknown site {cell.site_id!r}")
            cells_per_site[parent] = cells_per_site.get(parent, 0) + 1
            if cell.technology not in technologies:
                problems.append(f"cells: {cell.cell_id} has unknown technology {cell.technology!r}")
        for identity in sites:
            if cells_per_site.get(identity, 0) == 0:
                problems.append(f"sites: {identity[1]} hosts no cell")

        equipment_types = {member.value for member in EquipmentType}
        for item in self.equipment:
            host = sites.get((item.tenant_id, item.site_id))
            if host is None:
                problems.append(
                    f"equipment: {item.equipment_id} references unknown site {item.site_id!r}"
                )
            elif item.installed_date < host.commissioned_date:
                problems.append(
                    f"equipment: {item.equipment_id} installed {item.installed_date} "
                    f"before its site was commissioned {host.commissioned_date}"
                )
            if item.equipment_type not in equipment_types:
                problems.append(
                    f"equipment: {item.equipment_id} has unknown type {item.equipment_type!r}"
                )
            if item.last_maintenance_date < item.installed_date:
                problems.append(f"equipment: {item.equipment_id} maintained before being installed")

        bundle_types = {member.value for member in BundleType}
        for bundle in self.bundles:
            if bundle.bundle_type not in bundle_types:
                problems.append(
                    f"bundles: {bundle.bundle_code} has unknown type {bundle.bundle_type!r}"
                )

        domains = {member.value for member in AlarmDomain}
        severities = {member.value for member in AlarmSeverity}
        for alarm in self.alarm_types:
            if alarm.domain not in domains:
                problems.append(
                    f"alarm_types: {alarm.alarm_code} has unknown domain {alarm.domain!r}"
                )
            if alarm.default_severity not in severities:
                problems.append(
                    f"alarm_types: {alarm.alarm_code} has unknown severity {alarm.default_severity!r}"
                )

        return problems

    # -- Contract rules that need reference data -----------------------------

    def check_record(self, spec: ContractSpec, record: Mapping[str, Any]) -> list[str]:
        """Check the ``in_ref`` and ``lte_ref`` rules of ``spec`` against ``record``.

        These are the rules a Pydantic model cannot enforce, since they depend on
        what exists in the network rather than on the record alone.

        Args:
            spec: the contract the record belongs to.
            record: the event, as a mapping of field name to value.

        Returns:
            One message per violation; empty when the record is consistent with the
            catalogue. ``None`` values are skipped: an absent optional field refers
            to nothing.
        """
        problems: list[str] = []
        tenant_id = record.get("tenant_id")
        tenant = str(tenant_id) if tenant_id is not None else None

        referencing: dict[str, list[str]] = {}
        for fld in spec.fields:
            in_ref = fld.constraint(ConstraintKind.IN_REF)
            value = record.get(fld.name)
            if in_ref is None or value is None:
                continue
            table = str(in_ref.value)
            referencing.setdefault(table, []).append(fld.name)
            if table not in _BY_NAME:
                problems.append(f"{fld.name}: contract refers to unknown table {table!r}")
            elif _BY_NAME[table].tenant_scoped and tenant is None:
                problems.append(f"{fld.name}: cannot resolve {table!r} without a tenant_id")
            elif not self.has(table, str(value), tenant):
                problems.append(f"{fld.name}={value!r} does not exist in {table}")

        for fld in spec.fields:
            bound = fld.constraint(ConstraintKind.LTE_REF)
            value = record.get(fld.name)
            if bound is None or value is None:
                continue
            table, _, column = str(bound.value).partition(".")
            owners = [
                f.name
                for f in spec.fields
                if (c := f.constraint(ConstraintKind.IN_REF)) and c.value == table
            ]
            if len(owners) != 1:
                problems.append(
                    f"{fld.name}: lte_ref:{bound.value} is ambiguous; exactly one field must carry "
                    f"in_ref:{table}, found {owners}"
                )
                continue
            key = record.get(owners[0])
            if (
                key is None
                or table not in _BY_NAME
                or (_BY_NAME[table].tenant_scoped and tenant is None)
            ):
                continue
            row = self.get(table, str(key), tenant)
            if row is None:
                continue  # already reported as a missing reference above
            limit = getattr(row, column)
            if value > limit:
                problems.append(f"{fld.name}={value} exceeds {table}.{column}={limit} of {key!r}")

        return problems
