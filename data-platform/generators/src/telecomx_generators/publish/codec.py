"""Avro binary encoding in the Confluent wire format.

A message value is ``0x00`` (magic byte), the 4-byte big-endian id of the schema in the
registry, then the Avro-encoded record. The schema itself is not carried: a consumer reads
the id and fetches the schema once. That is what keeps messages small (ADR-015).
"""

from __future__ import annotations

import io
import json
import struct
from collections.abc import Mapping
from enum import Enum
from typing import Any, Final

import fastavro

__all__ = ["MAGIC_BYTE", "AvroCodec", "CodecError", "frame", "to_avro_record", "unframe"]

MAGIC_BYTE: Final = 0
_HEADER: Final = struct.Struct(">bI")


class CodecError(ValueError):
    """A value cannot be encoded, or a message is not in the wire format."""


def frame(schema_id: int, payload: bytes) -> bytes:
    """Prefix an Avro ``payload`` with the magic byte and the schema id."""
    return _HEADER.pack(MAGIC_BYTE, schema_id) + payload


def unframe(message: bytes) -> tuple[int, bytes]:
    """Split a wire-format message into ``(schema_id, avro_payload)``."""
    if len(message) < _HEADER.size or message[0] != MAGIC_BYTE:
        msg = "not a Confluent wire-format message (missing magic byte or schema id)"
        raise CodecError(msg)
    _, schema_id = _HEADER.unpack_from(message)
    return int(schema_id), message[_HEADER.size :]


def to_avro_record(values: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a model's field values into what Avro encoders accept.

    Enumerations become their symbol; everything else (UTC datetimes, ``Decimal``,
    ``None``) is already in the form ``fastavro`` expects.
    """
    return {k: v.value if isinstance(v, Enum) else v for k, v in values.items()}


class AvroCodec:
    """Encodes and decodes records of one schema, without the wire-format prefix."""

    def __init__(self, schema_text: str) -> None:
        try:
            self._schema = fastavro.parse_schema(json.loads(schema_text))
        except Exception as exc:  # fastavro raises a variety of types for bad schemas
            msg = f"invalid Avro schema: {exc}"
            raise CodecError(msg) from exc

    def encode(self, record: Mapping[str, Any]) -> bytes:
        """Avro-encode ``record``.

        Raises:
            CodecError: if the record does not match the schema (missing field, wrong type,
                unknown enum symbol, decimal that does not fit its precision).
        """
        buffer = io.BytesIO()
        try:
            fastavro.schemaless_writer(buffer, self._schema, dict(record))
        except Exception as exc:
            msg = f"record does not match the schema: {type(exc).__name__}"
            raise CodecError(msg) from exc
        return buffer.getvalue()

    def decode(self, payload: bytes) -> dict[str, Any]:
        """Decode an Avro ``payload`` produced with the same schema."""
        try:
            record = fastavro.schemaless_reader(io.BytesIO(payload), self._schema, None)
        except Exception as exc:
            msg = f"payload cannot be decoded with this schema: {type(exc).__name__}"
            raise CodecError(msg) from exc
        # Every contract is a record; say so rather than assume it.
        if not isinstance(record, dict):
            msg = "payload did not decode to a record"
            raise CodecError(msg)
        return {str(name): value for name, value in record.items()}
