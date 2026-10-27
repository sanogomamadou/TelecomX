"""Command line: write or verify the reference data committed as dbt seeds.

Usage:
    uv run python -m telecomx_generators.reference             # write
    uv run python -m telecomx_generators.reference --check     # CI: fail if stale

Exit codes: 0 success, 1 the committed files differ from the generator output
(``--check``), 2 the generator failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from telecom_common.reference import CatalogError
from telecomx_generators.reference.build import DEFAULT_SEED, build_catalog

__all__ = ["DEFAULT_OUT_DIR", "main"]

REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OUT_DIR = REPO_ROOT / "data-platform" / "dbt" / "seeds"


def _relative(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic reference data.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit 1 if the files differ from what the generator produces.",
    )
    args = parser.parse_args(argv)

    try:
        outputs = build_catalog(args.seed).render_csv()
    except CatalogError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2

    if args.check:
        problems = []
        for name, content in outputs.items():
            path = args.out_dir / name
            if not path.is_file():
                problems.append(f"missing   {_relative(path)}")
            elif path.read_bytes().decode("utf-8") != content:
                problems.append(f"outdated  {_relative(path)}")
        if problems:
            sys.stderr.write(f"Reference data is out of date (seed {args.seed}):\n")
            for problem in problems:
                sys.stderr.write(f"  {problem}\n")
            sys.stderr.write("Run: make refdata   (or: .\\make.ps1 refdata)\n")
            return 1
        sys.stdout.write(f"OK: {len(outputs)} reference files are up to date (seed {args.seed})\n")
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, content in outputs.items():
        path = args.out_dir / name
        if path.is_file() and path.read_bytes().decode("utf-8") == content:
            continue
        # newline="\n": the repository is LF everywhere, including on Windows.
        path.write_text(content, encoding="utf-8", newline="\n")
        written += 1
    sys.stdout.write(
        f"Generated {len(outputs)} reference files into {_relative(args.out_dir)} "
        f"({written} written, seed {args.seed})\n"
    )
    return 0
