"""Read access to the generated contract manifest.

The manifest is the language-neutral view of every contract. Python consumers (the
Spark silver job, the quality checks) load it here; non-Python consumers (dbt) read
the JSON file directly.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from telecom_common.contracts.spec import ContractSpec

__all__ = ["load_manifest", "manifest_path_text"]


def manifest_path_text() -> str:
    """Return the raw JSON text of the packaged manifest."""
    # Imported lazily: the generated package does not exist until the generator
    # has run, and the generator itself imports this package.
    package = resources.files("telecom_common.contracts.generated")
    return package.joinpath("manifest.json").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, ContractSpec]:
    """Return every contract, keyed by contract id (the ``.avsc`` file stem).

    Cached: the manifest is generated code and cannot change while a process runs.
    """
    document: dict[str, Any] = json.loads(manifest_path_text())
    return {
        contract_id: ContractSpec.from_dict(contract_id, data)
        for contract_id, data in document["contracts"].items()
    }
