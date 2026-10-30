"""A minimal client of the Confluent Schema Registry REST API.

Only what the platform needs: register a schema, enforce a compatibility level per
subject, ask whether a candidate schema would be accepted. It is written against the
standard library on purpose. The official client pulls in an HTTP stack, an OAuth
library and a cache, none of which this use needs, and a 100-line client is easy to
test against a fake server (see ``tests/unit/generators/test_publish.py``).

Subjects follow the default ``TopicNameStrategy``: the schema of topic ``cdr.events``
is the subject ``cdr.events-value``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Final

__all__ = ["CONTENT_TYPE", "RegistryError", "SchemaRegistryClient", "subject_for"]

CONTENT_TYPE: Final = "application/vnd.schemaregistry.v1+json"
LEVELS: Final = frozenset(
    {
        "BACKWARD",
        "BACKWARD_TRANSITIVE",
        "FORWARD",
        "FORWARD_TRANSITIVE",
        "FULL",
        "FULL_TRANSITIVE",
        "NONE",
    }
)


class RegistryError(RuntimeError):
    """The registry refused a request, or could not be reached.

    ``status`` is the HTTP status when the registry answered, ``None`` when it did not.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def subject_for(topic: str) -> str:
    """The registry subject holding the value schema of ``topic``."""
    return f"{topic}-value"


class SchemaRegistryClient:
    """Talks to a Schema Registry at ``url`` (for example ``http://localhost:8081``)."""

    def __init__(self, url: str, *, timeout: float = 10.0) -> None:
        if not url.startswith(("http://", "https://")):
            msg = f"schema registry URL must be http(s), got {url!r}"
            raise ValueError(msg)
        self._base = url.rstrip("/")
        self._timeout = timeout

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - scheme checked in __init__
            f"{self._base}{path}",
            data=data,
            method=method,
            headers={"Content-Type": CONTENT_TYPE, "Accept": CONTENT_TYPE},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise RegistryError(self._explain(exc), exc.code) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            msg = f"schema registry unreachable at {self._base}: {exc}"
            raise RegistryError(msg) from exc
        return json.loads(payload) if payload else None

    @staticmethod
    def _explain(exc: urllib.error.HTTPError) -> str:
        try:
            detail = json.loads(exc.read()).get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        return f"schema registry answered {exc.code}: {detail or exc.reason}"

    def ping(self) -> None:
        """Raise :class:`RegistryError` unless the registry answers."""
        self._request("GET", "/subjects")

    def register(self, subject: str, schema_text: str) -> int:
        """Register ``schema_text`` under ``subject`` and return its global id.

        Registering a schema already known returns its existing id: the call is idempotent.
        An incompatible evolution is refused by the registry (HTTP 409), which surfaces here
        as a :class:`RegistryError` with ``status == 409``.
        """
        path = f"/subjects/{urllib.parse.quote(subject, safe='')}/versions"
        answer = self._request("POST", path, {"schema": schema_text, "schemaType": "AVRO"})
        return int(answer["id"])

    def is_compatible(self, subject: str, schema_text: str) -> bool:
        """Whether ``schema_text`` could be registered under ``subject`` right now.

        A subject that does not exist yet has nothing to be incompatible with.
        """
        path = f"/compatibility/subjects/{urllib.parse.quote(subject, safe='')}/versions/latest"
        try:
            answer = self._request("POST", path, {"schema": schema_text, "schemaType": "AVRO"})
        except RegistryError as exc:
            if exc.status == 404:
                return True
            raise
        return bool(answer["is_compatible"])

    def set_compatibility(self, subject: str, level: str) -> None:
        """Pin the compatibility level of one subject, overriding the registry default."""
        if level not in LEVELS:
            msg = f"unknown compatibility level {level!r}"
            raise ValueError(msg)
        self._request(
            "PUT", f"/config/{urllib.parse.quote(subject, safe='')}", {"compatibility": level}
        )

    def get_compatibility(self, subject: str) -> str | None:
        """The level pinned on ``subject``, or ``None`` when it inherits the default."""
        try:
            answer = self._request(
                "GET", f"/config/{urllib.parse.quote(subject, safe='')}?defaultToGlobal=false"
            )
        except RegistryError as exc:
            if exc.status == 404:
                return None
            raise
        return str(answer["compatibilityLevel"])

    def latest(self, subject: str) -> dict[str, Any]:
        """The latest registered version of ``subject``: ``id``, ``version``, ``schema``."""
        answer = self._request(
            "GET", f"/subjects/{urllib.parse.quote(subject, safe='')}/versions/latest"
        )
        return dict(answer)

    def delete_subject(self, subject: str) -> None:
        """Remove a subject and all its versions, permanently (test clean-up only).

        The registry requires a soft delete before a permanent one. Never call this on a
        subject that consumers read: it destroys the schemas their messages point to.
        """
        path = f"/subjects/{urllib.parse.quote(subject, safe='')}"
        self._request("DELETE", path)
        self._request("DELETE", f"{path}?permanent=true")

    def subjects(self) -> list[str]:
        """Every subject known to the registry."""
        return [str(s) for s in self._request("GET", "/subjects")]
