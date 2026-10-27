"""Data contracts: parsing, code generation inputs, and generated event models.

``data-platform/schemas/avro/*.avsc`` is the single source of truth (ADR-015). This
package turns those files into everything else the platform needs:

* :mod:`.constraints` and :mod:`.spec` -- parse a contract into a typed model;
* :mod:`.codegen` -- render Pydantic models and a JSON manifest from it;
* :mod:`.base` -- the hand-written validation logic those models inherit;
* :mod:`.manifest` -- read the generated manifest back at runtime;
* ``generated/`` -- the output. Never edited by hand; CI regenerates and compares.

Deliberately not imported here: the ``generated`` subpackage, which does not exist
until the generator has run and which the generator itself depends on this package
to produce.
"""

from __future__ import annotations

from telecom_common.contracts.base import (
    NOT_FUTURE_TOLERANCE,
    SUM_TOLERANCE,
    ContractModel,
    UtcDatetime,
    not_in_future,
)
from telecom_common.contracts.constraints import (
    Constraint,
    ConstraintKind,
    ConstraintSyntaxError,
    parse_constraints,
)
from telecom_common.contracts.manifest import load_manifest
from telecom_common.contracts.spec import (
    ContractError,
    ContractSpec,
    FieldSpec,
    FieldType,
    parse_avsc,
)

__all__ = [
    "NOT_FUTURE_TOLERANCE",
    "SUM_TOLERANCE",
    "Constraint",
    "ConstraintKind",
    "ConstraintSyntaxError",
    "ContractError",
    "ContractModel",
    "ContractSpec",
    "FieldSpec",
    "FieldType",
    "UtcDatetime",
    "load_manifest",
    "not_in_future",
    "parse_avsc",
    "parse_constraints",
]
