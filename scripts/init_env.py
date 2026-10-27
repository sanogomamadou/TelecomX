#!/usr/bin/env python
"""Create ``.env`` from ``.env.example`` and fill the local secrets that are still empty.

Run through ``make env`` (or ``.\\make.ps1 env``). Idempotent: a value that is already set
is never touched, so re-running cannot rotate a key and silently break token continuity.

Secrets are random and generated on this machine; they are never printed. Only the
*names* of the variables that were filled are reported.

Exit codes: 0 success, 2 ``.env.example`` is missing.
"""

from __future__ import annotations

import re
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

__all__ = ["GENERATED_SECRETS", "fill_secrets", "main"]

#: Variables that must hold a random value for the local stack to start.
GENERATED_SECRETS = (
    "S3_SECRET_KEY",
    "POSTGRES_PASSWORD",
    "PII_TOKENIZATION_KEY",
    "PII_VAULT_ENCRYPTION_KEY",
)

ROOT = Path(__file__).resolve().parents[1]


def _new_secret() -> str:
    return secrets.token_urlsafe(48)


def fill_secrets(text: str, make_secret: Callable[[], str] = _new_secret) -> tuple[str, list[str]]:
    """Return ``text`` with every empty secret filled, and the names that were filled.

    A variable counts as empty when nothing but whitespace and an optional comment follows
    the ``=``. A comment is kept, so the template's explanations survive.
    """
    filled: list[str] = []
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        for name in GENERATED_SECRETS:
            match = re.fullmatch(rf"({re.escape(name)}=)[ \t]*(#.*?)?(\r?\n)?", line)
            if match is None:
                continue
            comment = f"  {match.group(2)}" if match.group(2) else ""
            lines[index] = f"{match.group(1)}{make_secret()}{comment}{match.group(3) or ''}"
            filled.append(name)
    return "".join(lines), filled


def main(root: Path = ROOT) -> int:
    template, target = root / ".env.example", root / ".env"
    if not template.is_file():
        sys.stderr.write(f"ERROR: {template} not found\n")
        return 2

    created = not target.exists()
    source = template.read_text(encoding="utf-8") if created else target.read_text(encoding="utf-8")
    text, filled = fill_secrets(source)
    if created or filled:
        target.write_text(text, encoding="utf-8", newline="\n")

    sys.stdout.write(".env created\n" if created else ".env already present\n")
    for name in filled:
        sys.stdout.write(f"  generated {name}\n")
    if not filled:
        sys.stdout.write("  no empty local secret to generate\n")
    sys.stdout.write("Remember to set GROQ_API_KEY yourself (Phase 4).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
