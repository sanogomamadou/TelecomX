"""Checks of the Kafka topic declarations and of the script that applies them."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from telecom_common.contracts.generated import BY_TOPIC

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
TOPICS_FILE = ROOT / "data-platform" / "kafka" / "topics.conf"
SCRIPT = ROOT / "data-platform" / "kafka" / "create-topics.sh"
ADR_002 = next((ROOT / "docs" / "adr").glob("ADR-002*.md"))


def _topics() -> dict[str, tuple[int, int, int]]:
    """topic -> (partitions, dev retention hours, prod retention days)."""
    parsed: dict[str, tuple[int, int, int]] = {}
    for line in TOPICS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, partitions, hours, days = line.split()
        assert name not in parsed, f"{name} declared twice"
        parsed[name] = (int(partitions), int(hours), int(days))
    return parsed


class TestDeclarations:
    def test_every_contract_topic_is_declared(self) -> None:
        assert set(BY_TOPIC) <= set(_topics())

    def test_every_topic_has_a_dead_letter_queue_and_nothing_else_exists(self) -> None:
        expected = set(BY_TOPIC) | {f"{t}.dlq" for t in BY_TOPIC}
        assert set(_topics()) == expected

    def test_dead_letter_queues_are_small_and_kept_longer(self) -> None:
        for name, (partitions, hours, days) in _topics().items():
            if name.endswith(".dlq"):
                assert partitions == 3, name
                assert hours == 72, name
                assert days == 30, name

    def test_ordinary_topics_keep_dev_retention_short(self) -> None:
        assert {h for n, (_, h, _) in _topics().items() if not n.endswith(".dlq")} == {24}

    def test_values_are_sane(self) -> None:
        for name, (partitions, hours, days) in _topics().items():
            assert 1 <= partitions <= 48, name
            assert hours >= 1 and days >= 1, name

    def test_partitions_and_prod_retention_match_adr_002(self) -> None:
        """The ADR is the decision; the file is its implementation. They must not drift."""
        rows = re.findall(
            r"^\| `([a-z.]+)` \| (\d+) \| (\d+) j \|",
            ADR_002.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        topics = _topics()
        compared = 0
        for name, partitions, days in rows:
            if name in topics:
                compared += 1
                assert topics[name][0] == int(partitions), f"{name}: partitions differ from ADR-002"
                assert topics[name][2] == int(days), f"{name}: retention differs from ADR-002"
        assert compared == len(BY_TOPIC)  # all eleven topics are in the ADR


def _find_bash() -> str | None:
    """A bash that actually runs. On Windows, `bash` on PATH is often the WSL launcher, which
    fails when no distribution is installed: probe instead of trusting `which`."""
    candidates = [shutil.which("bash"), "C:/Program Files/Git/bin/bash.exe", "/bin/bash"]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        probe = subprocess.run(  # noqa: S603
            [candidate, "-c", "echo ok"], capture_output=True, text=True, check=False
        )
        if probe.returncode == 0 and probe.stdout.strip() == "ok":
            return candidate
    return None


BASH = _find_bash()


@pytest.mark.skipif(BASH is None, reason="no working bash on this machine")
class TestCreateScript:
    def _run(
        self, tmp_path: Path, conf: Path
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the real script against fake kafka tools that only record their arguments."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = tmp_path / "calls.log"
        for tool in ("kafka-topics.sh", "kafka-configs.sh"):
            stub = bin_dir / tool
            stub.write_text(
                f'#!/bin/bash\necho "{tool} $*" >> "{log.as_posix()}"\n',
                encoding="utf-8",
                newline="\n",
            )
            stub.chmod(0o755)
        env = {
            **os.environ,
            "BOOTSTRAP_SERVERS": "kafka:19092",
            "TOPICS_FILE": conf.as_posix(),
            "KAFKA_BIN": bin_dir.as_posix(),
        }
        result = subprocess.run(  # noqa: S603
            [str(BASH), SCRIPT.as_posix()], env=env, capture_output=True, text=True, check=False
        )
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return result, calls

    def test_creates_every_declared_topic_with_its_settings(self, tmp_path: Path) -> None:
        result, calls = self._run(tmp_path, TOPICS_FILE)
        assert result.returncode == 0, result.stderr
        creates = [c for c in calls if "--create" in c]
        assert len(creates) == len(_topics())
        cdr = next(c for c in creates if "--topic cdr.events " in c + " ")
        assert "--partitions 24" in cdr
        assert "--replication-factor 1" in cdr
        assert f"retention.ms={24 * 3600 * 1000}" in cdr
        dlq = next(c for c in creates if "--topic cdr.events.dlq " in c + " ")
        assert f"retention.ms={72 * 3600 * 1000}" in dlq

    def test_reapplies_retention_to_existing_topics(self, tmp_path: Path) -> None:
        _, calls = self._run(tmp_path, TOPICS_FILE)
        alters = [c for c in calls if "--alter" in c]
        assert len(alters) == len(_topics())

    def test_comments_and_blank_lines_are_ignored(self, tmp_path: Path) -> None:
        conf = tmp_path / "small.conf"
        conf.write_text(
            "# a comment\n\nalpha 2 5 1\n   # indented comment\nbeta 3 1 1\n",
            encoding="utf-8",
            newline="\n",
        )
        result, calls = self._run(tmp_path, conf)
        assert result.returncode == 0, result.stderr
        assert [c for c in calls if "--create" in c and "--topic alpha " in c + " "]
        assert len([c for c in calls if "--create" in c]) == 2

    def test_a_windows_checkout_of_the_topics_file_still_works(self, tmp_path: Path) -> None:
        """Without dropping the carriage returns, the script created a topic named CR."""
        conf = tmp_path / "crlf.conf"
        conf.write_bytes(b"# c\r\n\r\nalpha 2 5 1\r\nbeta 3 1 1\r\n")
        result, calls = self._run(tmp_path, conf)
        assert result.returncode == 0, result.stderr
        topics = [c.split("--topic ")[1].split()[0] for c in calls if "--create" in c]
        assert topics == ["alpha", "beta"]

    def test_a_missing_variable_stops_the_script(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k not in {"BOOTSTRAP_SERVERS", "TOPICS_FILE"}}
        result = subprocess.run(  # noqa: S603
            [str(BASH), SCRIPT.as_posix()], env=env, capture_output=True, text=True, check=False
        )
        assert result.returncode != 0
        assert "BOOTSTRAP_SERVERS" in result.stderr

    def test_script_is_syntactically_valid(self) -> None:
        for script in (SCRIPT, ROOT / "infrastructure" / "docker" / "minio-init.sh"):
            result = subprocess.run(  # noqa: S603
                [str(BASH), "-n", script.as_posix()], capture_output=True, text=True, check=False
            )
            assert result.returncode == 0, result.stderr


class TestLineEndings:
    """Containers run these files on Linux: a CRLF checkout turns `bash\\r` into a failure."""

    @pytest.mark.parametrize(
        "path",
        [
            SCRIPT,
            TOPICS_FILE,
            ROOT / "docker-compose.yml",
            ROOT / "infrastructure" / "docker" / "minio-init.sh",
        ],
        ids=lambda p: p.name,
    )
    def test_file_uses_unix_line_endings(self, path: Path) -> None:
        assert b"\r" not in path.read_bytes()

    def test_git_attributes_pin_them(self) -> None:
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        assert "*.sh text eol=lf" in attributes
        assert "topics.conf text eol=lf" in attributes
