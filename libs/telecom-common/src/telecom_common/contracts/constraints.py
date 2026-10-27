"""Parser for the ``telecomx.constraint`` mini-language used in data contracts.

A constraint attribute looks like ``"not_null,pattern:^SITE-[0-9]{3,4}$,in_ref:sites"``.
This module turns that string into typed :class:`Constraint` objects, which are the
single representation consumed by the model generator, the Spark quality job and
the dbt test generator. Parsing it once, here, is what keeps those three from each
interpreting the same rule slightly differently.

Why not ``text.split(",")``
---------------------------
The grammar is comma-separated, but a regular expression may itself contain commas:
``[0-9]{3,4}`` is a quantifier, not two constraints. A naive split silently turns
one valid pattern into a broken pattern plus a bogus constraint named ``4}$``.
:func:`split_top_level` therefore only splits on commas that sit outside braces,
parentheses and character classes.

Two kinds of rules
------------------
Some rules can be checked on a single record (``gte``, ``pattern``, ``eq_sum``);
others need state that a single record does not carry: history for ``unique``,
reference tables for ``in_ref`` and ``lte_ref``. :attr:`Constraint.needs_context`
separates them, because the generated Pydantic models enforce only the former and
leave the latter to Spark and dbt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "Constraint",
    "ConstraintKind",
    "ConstraintSyntaxError",
    "ConstraintValue",
    "parse_constraints",
    "split_top_level",
]

#: A constraint's argument: a number (``gte:0``), a string (``in_ref:cells``), a
#: tuple of field names (``eq_sum:a+b``), or nothing (``not_null``).
ConstraintValue = int | float | str | tuple[str, ...] | None


class ConstraintSyntaxError(ValueError):
    """Raised when a ``telecomx.constraint`` string is malformed."""


class ConstraintKind(StrEnum):
    """Every rule the contract grammar supports.

    Adding a kind means teaching the generator, the Spark job and the dbt test
    generator about it. It is deliberately a closed set: an unknown name is a
    typo to catch at build time, not something to ignore.
    """

    NOT_NULL = "not_null"
    UNIQUE = "unique"
    NOT_FUTURE = "not_future"
    GTE = "gte"
    GT = "gt"
    LTE = "lte"
    LT = "lt"
    PATTERN = "pattern"
    IN_REF = "in_ref"
    LTE_REF = "lte_ref"
    EQ_SUM = "eq_sum"


_FLAGS: Final = frozenset(
    {ConstraintKind.NOT_NULL, ConstraintKind.UNIQUE, ConstraintKind.NOT_FUTURE}
)
_NUMERIC: Final = frozenset(
    {ConstraintKind.GTE, ConstraintKind.GT, ConstraintKind.LTE, ConstraintKind.LT}
)
_NEEDS_CONTEXT: Final = frozenset(
    {ConstraintKind.UNIQUE, ConstraintKind.IN_REF, ConstraintKind.LTE_REF}
)

_IDENTIFIER: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_TABLE_COLUMN: Final = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class Constraint:
    """One parsed rule, e.g. ``Constraint(GTE, 0)`` or ``Constraint(NOT_NULL)``."""

    kind: ConstraintKind
    value: ConstraintValue = None

    @property
    def needs_context(self) -> bool:
        """Whether checking this rule requires data outside the record itself.

        ``unique`` needs history, ``in_ref`` and ``lte_ref`` need reference
        tables. Such rules cannot be enforced by a Pydantic model and belong to
        the Spark quality job and to dbt tests.
        """
        return self.kind in _NEEDS_CONTEXT

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-friendly mapping (used by the manifest)."""
        payload: dict[str, object] = {"kind": self.kind.value}
        if self.value is not None:
            payload["value"] = list(self.value) if isinstance(self.value, tuple) else self.value
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Constraint:
        """Inverse of :meth:`to_dict`."""
        raw = data.get("value")
        value: ConstraintValue
        if isinstance(raw, list):
            value = tuple(str(item) for item in raw)
        elif raw is None or isinstance(raw, (int, float, str)):
            value = raw
        else:
            msg = f"unsupported constraint value {raw!r}"
            raise ConstraintSyntaxError(msg)
        return cls(kind=ConstraintKind(str(data["kind"])), value=value)


def split_top_level(text: str) -> list[str]:
    """Split ``text`` on commas that are not inside ``{}``, ``()`` or ``[]``.

    Backslash escapes the next character, so ``\\,`` and ``\\]`` inside a pattern
    are treated literally.

    Raises:
        ConstraintSyntaxError: on unbalanced brackets, which would otherwise
            swallow the rest of the string into a single constraint.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_class = False
    escaped = False

    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if in_class:
            if char == "]":
                in_class = False
            current.append(char)
            continue
        if char == "[":
            in_class = True
        elif char in "{(":
            depth += 1
        elif char in "})":
            depth -= 1
            if depth < 0:
                msg = f"unbalanced closing bracket in constraint {text!r}"
                raise ConstraintSyntaxError(msg)
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)

    if in_class or depth != 0:
        msg = f"unbalanced brackets in constraint {text!r}"
        raise ConstraintSyntaxError(msg)

    parts.append("".join(current))
    return [part.strip() for part in parts]


def _parse_number(token: str, argument: str) -> int | float:
    try:
        return int(argument)
    except ValueError:
        pass
    try:
        return float(argument)
    except ValueError:
        msg = f"constraint {token!r}: {argument!r} is not a number"
        raise ConstraintSyntaxError(msg) from None


def _parse_token(token: str) -> Constraint:
    name, separator, argument = token.partition(":")
    name = name.strip()
    try:
        kind = ConstraintKind(name)
    except ValueError:
        known = ", ".join(sorted(k.value for k in ConstraintKind))
        msg = f"unknown constraint {name!r} in {token!r}; known constraints: {known}"
        raise ConstraintSyntaxError(msg) from None

    has_argument = bool(separator)

    if kind in _FLAGS:
        if has_argument:
            msg = f"constraint {name!r} takes no argument, got {token!r}"
            raise ConstraintSyntaxError(msg)
        return Constraint(kind)

    if not has_argument or not argument:
        msg = f"constraint {name!r} requires an argument, e.g. {name}:<value>"
        raise ConstraintSyntaxError(msg)

    if kind in _NUMERIC:
        return Constraint(kind, _parse_number(token, argument))

    if kind is ConstraintKind.PATTERN:
        try:
            re.compile(argument)
        except re.error as exc:
            msg = f"constraint {token!r}: invalid regular expression ({exc})"
            raise ConstraintSyntaxError(msg) from None
        return Constraint(kind, argument)

    if kind is ConstraintKind.IN_REF:
        if not _IDENTIFIER.match(argument):
            msg = f"constraint {token!r}: {argument!r} is not a valid reference name"
            raise ConstraintSyntaxError(msg)
        return Constraint(kind, argument)

    if kind is ConstraintKind.LTE_REF:
        if not _TABLE_COLUMN.match(argument):
            msg = f"constraint {token!r}: expected <table>.<column>, got {argument!r}"
            raise ConstraintSyntaxError(msg)
        return Constraint(kind, argument)

    # EQ_SUM: the only remaining kind.
    operands = tuple(part.strip() for part in argument.split("+"))
    if len(operands) < 2 or not all(_IDENTIFIER.match(op) for op in operands):
        msg = f"constraint {token!r}: eq_sum needs two or more field names joined by '+'"
        raise ConstraintSyntaxError(msg)
    return Constraint(kind, operands)


def parse_constraints(text: str | None) -> tuple[Constraint, ...]:
    """Parse a full ``telecomx.constraint`` attribute value.

    Args:
        text: the raw attribute, or ``None``/empty when the field has none.

    Returns:
        The constraints in declaration order.

    Raises:
        ConstraintSyntaxError: on an unknown rule, a missing or superfluous
            argument, a malformed value, unbalanced brackets, or the same rule
            declared twice (``gte:0,gte:1`` is a contradiction waiting to be
            resolved by whichever parser happens to run last).
    """
    if text is None or not text.strip():
        return ()

    # An empty token comes from ",," or a trailing comma; _parse_token rejects it
    # as an unknown constraint rather than skipping it silently.
    constraints = [_parse_token(token) for token in split_top_level(text)]

    seen: set[ConstraintKind] = set()
    for constraint in constraints:
        if constraint.kind in seen:
            msg = f"constraint {constraint.kind.value!r} declared more than once in {text!r}"
            raise ConstraintSyntaxError(msg)
        seen.add(constraint.kind)
    return tuple(constraints)
