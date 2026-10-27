"""Tests for the ``telecomx.constraint`` mini-language parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from telecom_common.contracts.constraints import (
    Constraint,
    ConstraintKind,
    ConstraintSyntaxError,
    parse_constraints,
    split_top_level,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
AVRO_DIR = REPO_ROOT / "data-platform" / "schemas" / "avro"


class TestEmptyInput:
    @pytest.mark.parametrize("text", [None, "", "   "])
    def test_no_constraints(self, text: str | None) -> None:
        assert parse_constraints(text) == ()


class TestEachKind:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("not_null", Constraint(ConstraintKind.NOT_NULL)),
            ("unique", Constraint(ConstraintKind.UNIQUE)),
            ("not_future", Constraint(ConstraintKind.NOT_FUTURE)),
            ("gte:0", Constraint(ConstraintKind.GTE, 0)),
            ("gt:0", Constraint(ConstraintKind.GT, 0)),
            ("lte:100", Constraint(ConstraintKind.LTE, 100)),
            ("lt:5", Constraint(ConstraintKind.LT, 5)),
            ("gte:-140", Constraint(ConstraintKind.GTE, -140)),
            ("lte:1.5", Constraint(ConstraintKind.LTE, 1.5)),
            ("in_ref:cells", Constraint(ConstraintKind.IN_REF, "cells")),
            (
                "lte_ref:cells.capacity_users",
                Constraint(ConstraintKind.LTE_REF, "cells.capacity_users"),
            ),
            (
                "eq_sum:download_mb+upload_mb",
                Constraint(ConstraintKind.EQ_SUM, ("download_mb", "upload_mb")),
            ),
            ("pattern:^CELL-[0-9]{4}$", Constraint(ConstraintKind.PATTERN, "^CELL-[0-9]{4}$")),
        ],
    )
    def test_parses(self, text: str, expected: Constraint) -> None:
        assert parse_constraints(text) == (expected,)

    def test_integers_stay_integers_and_decimals_become_floats(self) -> None:
        (int_constraint,) = parse_constraints("gte:5")
        (float_constraint,) = parse_constraints("gte:5.0")
        assert isinstance(int_constraint.value, int)
        assert isinstance(float_constraint.value, float)

    def test_preserves_declaration_order(self) -> None:
        kinds = [c.kind for c in parse_constraints("gte:0,not_null,lte:10")]
        assert kinds == [ConstraintKind.GTE, ConstraintKind.NOT_NULL, ConstraintKind.LTE]

    def test_tolerates_whitespace_around_tokens(self) -> None:
        assert len(parse_constraints(" not_null , gte:0 ")) == 2


class TestCommaInsideAPattern:
    """The trap that motivated the dedicated splitter.

    ``[0-9]{3,4}`` contains a comma. Splitting naively on commas turns one valid
    pattern into a broken pattern plus a bogus constraint named ``4}$``.
    """

    TEXT = "not_null,pattern:^SITE-[0-9]{3,4}$,in_ref:sites"

    def test_pattern_survives_intact(self) -> None:
        constraints = parse_constraints(self.TEXT)
        assert [c.kind for c in constraints] == [
            ConstraintKind.NOT_NULL,
            ConstraintKind.PATTERN,
            ConstraintKind.IN_REF,
        ]
        assert constraints[1].value == "^SITE-[0-9]{3,4}$"

    def test_naive_split_would_have_failed(self) -> None:
        """Documents why split_top_level exists."""
        assert len(self.TEXT.split(",")) == 4
        assert len(split_top_level(self.TEXT)) == 3


class TestSplitTopLevel:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("a,b,c", ["a", "b", "c"]),
            ("a{1,2},b", ["a{1,2}", "b"]),
            ("a(1,2),b", ["a(1,2)", "b"]),
            ("a[,],b", ["a[,]", "b"]),
            ("a[(],b", ["a[(]", "b"]),  # a bracket inside a class is not nesting
            (r"a\,b,c", [r"a\,b", "c"]),
            ("single", ["single"]),
        ],
    )
    def test_splits_only_at_top_level(self, text: str, expected: list[str]) -> None:
        assert split_top_level(text) == expected

    @pytest.mark.parametrize("text", ["a{1,2", "a(1,2", "a[1,2", "a}", "a)"])
    def test_rejects_unbalanced_brackets(self, text: str) -> None:
        with pytest.raises(ConstraintSyntaxError, match="unbalanced"):
            split_top_level(text)


class TestRejection:
    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("gte:0,frobnicate", "unknown constraint 'frobnicate'"),
            ("not_null:1", "takes no argument"),
            ("gte", "requires an argument"),
            ("gte:", "requires an argument"),
            ("gte:abc", "is not a number"),
            ("pattern:[unclosed", "unbalanced"),
            ("pattern:(?P<broken", "unbalanced"),
            ("in_ref:Bad-Name", "not a valid reference name"),
            ("lte_ref:cells", "expected <table>.<column>"),
            ("eq_sum:only_one", "two or more field names"),
            ("eq_sum:a+Bad", "two or more field names"),
            ("not_null,,gte:0", "unknown constraint ''"),
            ("not_null,", "unknown constraint ''"),
        ],
    )
    def test_rejects_malformed(self, text: str, message: str) -> None:
        with pytest.raises(ConstraintSyntaxError, match=message):
            parse_constraints(text)

    def test_rejects_invalid_regular_expression(self) -> None:
        with pytest.raises(ConstraintSyntaxError, match="invalid regular expression"):
            parse_constraints("pattern:*oops")

    def test_rejects_duplicated_rule(self) -> None:
        """Two bounds of the same kind would be resolved by whichever parser ran last."""
        with pytest.raises(ConstraintSyntaxError, match="more than once"):
            parse_constraints("gte:0,gte:1")

    def test_error_lists_the_known_constraints(self) -> None:
        with pytest.raises(ConstraintSyntaxError, match=r"known constraints:.*not_null"):
            parse_constraints("nope")


class TestNeedsContext:
    @pytest.mark.parametrize(
        ("kind", "value", "expected"),
        [
            (ConstraintKind.UNIQUE, None, True),
            (ConstraintKind.IN_REF, "cells", True),
            (ConstraintKind.LTE_REF, "cells.capacity_users", True),
            (ConstraintKind.NOT_NULL, None, False),
            (ConstraintKind.GTE, 0, False),
            (ConstraintKind.PATTERN, "^x$", False),
            (ConstraintKind.EQ_SUM, ("a", "b"), False),
            (ConstraintKind.NOT_FUTURE, None, False),
        ],
    )
    def test_needs_outside_state(self, kind: ConstraintKind, value: object, expected: bool) -> None:
        """Only record-level rules can be enforced by a generated model."""
        assert Constraint(kind, value).needs_context is expected  # type: ignore[arg-type]


class TestSerialisation:
    @pytest.mark.parametrize(
        "text",
        ["not_null", "gte:0", "lte:1.5", "in_ref:cells", "eq_sum:a+b", "pattern:^SITE-[0-9]{3,4}$"],
    )
    def test_round_trips_through_dict(self, text: str) -> None:
        (constraint,) = parse_constraints(text)
        assert Constraint.from_dict(constraint.to_dict()) == constraint

    def test_round_trips_through_json(self) -> None:
        constraints = parse_constraints("not_null,pattern:^SITE-[0-9]{3,4}$,eq_sum:a+b")
        payload = json.dumps([c.to_dict() for c in constraints])
        restored = tuple(Constraint.from_dict(d) for d in json.loads(payload))
        assert restored == constraints

    def test_flag_has_no_value_key(self) -> None:
        assert Constraint(ConstraintKind.NOT_NULL).to_dict() == {"kind": "not_null"}

    def test_rejects_unsupported_value_type(self) -> None:
        with pytest.raises(ConstraintSyntaxError, match="unsupported constraint value"):
            Constraint.from_dict({"kind": "gte", "value": {"nested": 1}})


def _declared_constraints() -> list[tuple[str, str, str]]:
    """Every ``(contract, field, constraint text)`` declared in the repository."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(AVRO_DIR.glob("*.avsc")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        for fld in schema["fields"]:
            text = fld.get("telecomx.constraint")
            if text:
                found.append((path.stem, fld["name"], text))
    return found


class TestRealContracts:
    """Every constraint actually declared in the repository must parse."""

    def test_repository_declares_constraints(self) -> None:
        assert len(_declared_constraints()) > 50

    @pytest.mark.parametrize(
        ("contract", "field", "text"),
        _declared_constraints(),
        ids=lambda value: str(value)[:40],
    )
    def test_declared_constraint_parses(self, contract: str, field: str, text: str) -> None:
        assert parse_constraints(text), f"{contract}.{field}: {text!r}"
