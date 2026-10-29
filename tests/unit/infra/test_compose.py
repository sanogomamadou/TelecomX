"""Static checks of docker-compose.yml: the rules ADR-011 and the security docs impose.

These run without Docker. They cannot prove that the containers start; they prove that
the file cannot violate the rules which, once broken, fail silently (a JVM heap above its
container limit, a port open to the network, a secret with a default value).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "docker-compose.yml"

CORE_LONG_RUNNING = {"kafka", "schema-registry", "seaweedfs", "postgres", "redis"}
CORE_ONE_SHOT = {"kafka-init", "s3-init"}
#: ADR-011: the core profile is budgeted at 2.1 GB (decimal), with a little rounding room.
CORE_BUDGET_MB = 2200


def _memory_mb(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([mg])", str(value).lower())
    assert match, f"unreadable memory limit: {value!r}"
    return float(match.group(1)) * (1024 if match.group(2) == "g" else 1)


def _heap_mb(options: str, flag: str) -> float | None:
    match = re.search(rf"-{flag}(\d+)([mg])", options)
    if match is None:
        return None
    return float(match.group(1)) * (1024 if match.group(2) == "g" else 1)


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def services(compose: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return compose["services"]  # type: ignore[no-any-return]


def _env(service: dict[str, Any]) -> dict[str, str]:
    return {k: str(v) for k, v in service.get("environment", {}).items()}


class TestCoreProfile:
    def test_core_contains_exactly_the_planned_services(self, services: dict[str, Any]) -> None:
        core = {n for n, s in services.items() if "core" in s.get("profiles", [])}
        assert core == CORE_LONG_RUNNING | CORE_ONE_SHOT

    def test_no_service_starts_without_a_profile(self, services: dict[str, Any]) -> None:
        """A bare `docker compose up` must start nothing: the machine cannot afford it."""
        assert all(s.get("profiles") for s in services.values())

    def test_long_running_services_fit_the_budget(self, services: dict[str, Any]) -> None:
        total = sum(_memory_mb(services[n]["mem_limit"]) for n in CORE_LONG_RUNNING)
        assert total <= CORE_BUDGET_MB, f"core needs {total} MB, budget {CORE_BUDGET_MB} MB"

    def test_the_project_is_named(self, compose: dict[str, Any]) -> None:
        assert compose["name"] == "telecomx"


class TestMemory:
    def test_every_service_has_a_memory_limit(self, services: dict[str, Any]) -> None:
        assert [n for n, s in services.items() if "mem_limit" not in s] == []

    def test_every_jvm_heap_is_bounded_below_its_limit(self, services: dict[str, Any]) -> None:
        """The silent OOMKilled: a limit with no -Xmx, or an -Xmx at the limit."""
        checked = 0
        for name, service in services.items():
            for key, value in _env(service).items():
                if not key.endswith("HEAP_OPTS"):
                    continue
                checked += 1
                limit = _memory_mb(service["mem_limit"])
                heap = _heap_mb(value, "Xmx")
                assert heap is not None, f"{name}: {key} sets no -Xmx"
                assert heap <= 0.7 * limit, f"{name}: heap {heap} MB above 70% of {limit} MB"
                start = _heap_mb(value, "Xms")
                assert start is None or start <= heap, f"{name}: -Xms above -Xmx"
        assert checked >= 3  # kafka, kafka-init, schema-registry

    def test_every_jvm_service_declares_a_heap(self, services: dict[str, Any]) -> None:
        jvm = {"kafka", "kafka-init", "schema-registry"}
        for name in jvm:
            assert any(k.endswith("HEAP_OPTS") for k in _env(services[name])), name

    def test_redis_is_bounded_and_does_not_persist(self, services: dict[str, Any]) -> None:
        command = services["redis"]["command"]
        assert command[command.index("--maxmemory") + 1] == "64mb"
        assert command[command.index("--maxmemory-policy") + 1] == "allkeys-lru"
        assert command[command.index("--save") + 1] == ""


class TestSecurity:
    def test_published_ports_are_bound_to_localhost(self, services: dict[str, Any]) -> None:
        ports = [(n, p) for n, s in services.items() for p in s.get("ports", [])]
        assert ports
        assert [(n, p) for n, p in ports if not str(p).startswith("127.0.0.1:")] == []

    def test_secrets_have_no_default_value(self, services: dict[str, Any]) -> None:
        """`${VAR:?msg}` makes compose refuse to start; `${VAR:-x}` would invent a secret."""
        secret = re.compile(r"PASSWORD|SECRET|_KEY")
        seen = 0
        for name, service in services.items():
            for key, value in _env(service).items():
                if not secret.search(key):
                    continue
                seen += 1
                assert re.fullmatch(r"\$\{[A-Z0-9_]+:\?[^}]+\}", value), f"{name}.{key} = {value!r}"
        assert seen >= 3  # S3_ACCESS_KEY, S3_SECRET_KEY, POSTGRES_PASSWORD

    def test_required_variables_exist_in_the_template(self, services: dict[str, Any]) -> None:
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        lines = COMPOSE.read_text(encoding="utf-8").splitlines()
        code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
        required = set(re.findall(r"\$\{([A-Z0-9_]+):\?", code))
        assert required >= {"S3_ACCESS_KEY", "S3_SECRET_KEY", "POSTGRES_PASSWORD"}
        for variable in required:
            assert re.search(rf"^{variable}=", template, re.MULTILINE), variable

    def test_no_literal_credential_in_the_file(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8")
        assert not re.search(r"(PASSWORD|SECRET)[A-Z_]*:\s+[^$\s\"']", text)


class TestImages:
    def test_every_image_is_pinned(self, services: dict[str, Any]) -> None:
        for name, service in services.items():
            image = service["image"]
            assert ":" in image, f"{name}: {image} has no tag"
            assert not image.endswith(":latest"), f"{name}: floating tag"


class TestReliability:
    def test_long_running_services_have_healthchecks(self, services: dict[str, Any]) -> None:
        assert [n for n in CORE_LONG_RUNNING if "healthcheck" not in services[n]] == []

    def test_one_shot_jobs_wait_for_a_healthy_dependency_and_do_not_restart(
        self, services: dict[str, Any]
    ) -> None:
        for name, dependency in (("kafka-init", "kafka"), ("s3-init", "seaweedfs")):
            service = services[name]
            assert service["depends_on"][dependency]["condition"] == "service_healthy"
            assert service["restart"] == "no"

    def test_schema_registry_waits_for_kafka(self, services: dict[str, Any]) -> None:
        assert services["schema-registry"]["depends_on"]["kafka"]["condition"] == "service_healthy"

    def test_long_running_services_restart_unless_stopped(self, services: dict[str, Any]) -> None:
        assert [
            n for n in CORE_LONG_RUNNING if services[n].get("restart") != "unless-stopped"
        ] == []

    def test_state_lives_in_named_volumes(self, compose: dict[str, Any]) -> None:
        assert set(compose["volumes"]) == {"kafka-data", "seaweedfs-data", "postgres-data"}

    def test_mounted_files_exist(self, services: dict[str, Any]) -> None:
        for name, service in services.items():
            for volume in service.get("volumes", []):
                source = str(volume).split(":")[0]
                if source.startswith("./"):
                    assert (ROOT / source).is_file(), f"{name} mounts missing file {source}"


class TestKafkaBroker:
    def test_topics_are_never_created_by_accident(self, services: dict[str, Any]) -> None:
        assert _env(services["kafka"])["KAFKA_AUTO_CREATE_TOPICS_ENABLE"] == "false"

    def test_single_broker_replication_is_consistent(self, services: dict[str, Any]) -> None:
        env = _env(services["kafka"])
        for key in (
            "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR",
            "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR",
            "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR",
        ):
            assert env[key] == "1", key  # a value above 1 would stop the only broker from starting

    def test_containers_and_host_use_different_advertised_listeners(
        self, services: dict[str, Any]
    ) -> None:
        env = _env(services["kafka"])
        assert "INTERNAL://kafka:19092" in env["KAFKA_ADVERTISED_LISTENERS"]
        assert "HOST://localhost:9092" in env["KAFKA_ADVERTISED_LISTENERS"]
        assert env["KAFKA_INTER_BROKER_LISTENER_NAME"] == "INTERNAL"

    def test_clients_inside_compose_target_the_internal_listener(
        self, services: dict[str, Any]
    ) -> None:
        assert _env(services["kafka-init"])["BOOTSTRAP_SERVERS"] == "kafka:19092"
        assert (
            _env(services["schema-registry"])["SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS"]
            == "kafka:19092"
        )

    def test_host_port_matches_the_environment_template(self, services: dict[str, Any]) -> None:
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert "KAFKA_BOOTSTRAP_SERVERS=localhost:9092" in template
        assert "127.0.0.1:9092:9092" in services["kafka"]["ports"]

    def test_schema_registry_default_compatibility_is_backward(
        self, services: dict[str, Any]
    ) -> None:
        """ADR-015: ingestion topics evolve BACKWARD."""
        assert (
            _env(services["schema-registry"])["SCHEMA_REGISTRY_SCHEMA_COMPATIBILITY_LEVEL"]
            == "BACKWARD"
        )


class TestObjectStorage:
    """ADR-021: SeaweedFS behind the S3 API, after MinIO's images were withdrawn."""

    #: Repositories that no longer exist on Docker Hub (checked 2026-10-28). Referencing
    #: one makes `docker compose pull` fail, which is how this was discovered.
    WITHDRAWN = ("minio/minio", "minio/mc")

    def test_no_image_comes_from_a_withdrawn_repository(self, services: dict[str, Any]) -> None:
        used = {name: s["image"] for name, s in services.items()}
        bad = {n: i for n, i in used.items() if any(i.startswith(w) for w in self.WITHDRAWN)}
        assert bad == {}

    def test_s3_endpoint_of_the_template_matches_the_published_port(
        self, services: dict[str, Any]
    ) -> None:
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        endpoint = re.search(r"^S3_ENDPOINT=http://localhost:(\d+)", template, re.MULTILINE)
        assert endpoint, "S3_ENDPOINT missing from .env.example"
        assert f"127.0.0.1:{endpoint.group(1)}:8333" in services["seaweedfs"]["ports"]

    def test_go_memory_ceiling_is_below_the_container_limit(self, services: dict[str, Any]) -> None:
        checked = 0
        for name, service in services.items():
            ceiling = _env(service).get("GOMEMLIMIT")
            if ceiling is None:
                continue
            checked += 1
            match = re.fullmatch(r"(\d+)MiB", ceiling)
            assert match, f"{name}: unreadable GOMEMLIMIT {ceiling!r}"
            assert int(match.group(1)) <= 0.7 * _memory_mb(service["mem_limit"]), name
        assert checked == 2  # seaweedfs and s3-init

    def test_credentials_reach_the_container_only_through_the_environment(
        self, services: dict[str, Any]
    ) -> None:
        command = " ".join(services["seaweedfs"]["command"])
        assert "$$S3_ACCESS_KEY" in command and "$$S3_SECRET_KEY" in command
        assert not re.search(r"accessKey\":\"[A-Za-z0-9]{8,}", command)
