#!/usr/bin/env python3
"""Generate the event models and the manifest from the Avro data contracts.

Reads ``data-platform/schemas/avro/*.avsc`` and writes, into
``libs/telecom-common/src/telecom_common/contracts/generated/``:

* one Pydantic module per contract;
* ``__init__.py``, a registry of every model by contract id and by Kafka topic;
* ``manifest.json``, the language-neutral description consumed by Spark and dbt.

Usage:
    uv run python data-platform/schemas/generate.py            # write
    uv run python data-platform/schemas/generate.py --check    # CI: fail if stale

Exit codes: 0 success, 1 generated files are stale (``--check``), 2 a contract is invalid.

All the logic lives in ``telecom_common.contracts.codegen``, where it is unit
tested. This script only reads files and writes the result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from telecom_common.contracts.codegen import (
    is_generated,
    module_name,
    render_manifest,
    render_module,
    render_registry,
    validate_specs,
)
from telecom_common.contracts.spec import ContractError, ContractSpec, parse_avsc

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_DIR = REPO_ROOT / "data-platform" / "schemas" / "avro"
DEFAULT_OUT_DIR = (
    REPO_ROOT / "libs" / "telecom-common" / "src" / "telecom_common" / "contracts" / "generated"
)


def _relative(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def load_specs(schema_dir: Path) -> list[tuple[Path, ContractSpec]]:
    """Parse every ``.avsc`` in ``schema_dir``.

    Raises:
        ContractError: if any contract is invalid, or none is found.
    """
    paths = sorted(schema_dir.glob("*.avsc"))
    if not paths:
        msg = f"no .avsc contracts found in {schema_dir}"
        raise ContractError(msg)
    pairs = [
        (path, parse_avsc(path.stem, json.loads(path.read_text(encoding="utf-8"))))
        for path in paths
    ]
    validate_specs([spec for _, spec in pairs])
    return pairs


def build_outputs(schema_dir: Path, out_dir: Path) -> dict[Path, str]:
    """Return ``{output path: content}`` for everything the generator produces."""
    pairs = load_specs(schema_dir)
    specs = [spec for _, spec in pairs]
    outputs: dict[Path, str] = {
        out_dir / f"{module_name(spec.contract_id)}.py": render_module(spec, source=_relative(path))
        for path, spec in pairs
    }
    outputs[out_dir / "__init__.py"] = render_registry(specs)
    outputs[out_dir / "manifest.json"] = render_manifest(specs)
    return outputs


def stale_files(out_dir: Path, expected: set[Path]) -> list[Path]:
    """Generated modules on disk that no contract produces any more.

    Only files carrying the generated marker are considered, so a hand-written
    file that someone dropped in the directory is never deleted.
    """
    if not out_dir.is_dir():
        return []
    return [
        path
        for path in sorted(out_dir.glob("*.py"))
        if path not in expected and is_generated(path.read_text(encoding="utf-8"))
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--schema-dir", type=Path, default=DEFAULT_SCHEMA_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit 1 if the generated files differ from the contracts.",
    )
    args = parser.parse_args(argv)

    try:
        outputs = build_outputs(args.schema_dir, args.out_dir)
    except ContractError as exc:
        sys.stderr.write(f"ERROR: invalid contract: {exc}\n")
        return 2

    stale = stale_files(args.out_dir, set(outputs))

    if args.check:
        problems: list[str] = []
        for path, content in outputs.items():
            if not path.is_file():
                problems.append(f"missing   {_relative(path)}")
            elif path.read_bytes().decode("utf-8") != content:
                problems.append(f"outdated  {_relative(path)}")
        problems.extend(f"stale     {_relative(path)}" for path in stale)
        if problems:
            sys.stderr.write("Generated contract code is out of date:\n")
            for problem in problems:
                sys.stderr.write(f"  {problem}\n")
            sys.stderr.write("Run: make codegen   (or: .\\make.ps1 codegen)\n")
            return 1
        sys.stdout.write(f"OK: {len(outputs)} generated files are up to date\n")
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for path, content in outputs.items():
        if path.is_file() and path.read_bytes().decode("utf-8") == content:
            continue
        # newline="\n": the repository is LF everywhere, including on Windows.
        path.write_text(content, encoding="utf-8", newline="\n")
        written += 1
    for path in stale:
        path.unlink()
    sys.stdout.write(
        f"Generated {len(outputs)} files ({written} written, {len(stale)} stale removed)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
