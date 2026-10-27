"""Tests for scripts/init_env.py: local secrets are generated, never overwritten, never printed."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "init_env_script", ROOT / "scripts" / "init_env.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


init_env = _load()


def _counter():
    count = iter(range(1, 100))
    return lambda: f"generated-{next(count)}"


class TestFillSecrets:
    def test_fills_every_empty_secret(self) -> None:
        text = (
            "S3_SECRET_KEY=\nPOSTGRES_PASSWORD=\nPII_TOKENIZATION_KEY=\nPII_VAULT_ENCRYPTION_KEY=\n"
        )
        out, filled = init_env.fill_secrets(text, _counter())
        assert set(filled) == set(init_env.GENERATED_SECRETS)
        assert "=\n" not in out

    def test_never_overwrites_an_existing_value(self) -> None:
        text = "S3_SECRET_KEY=keep-me\nPOSTGRES_PASSWORD=also-me\n"
        out, filled = init_env.fill_secrets(text, _counter())
        assert out == text
        assert filled == []

    def test_keeps_the_comment_of_the_template(self) -> None:
        out, _ = init_env.fill_secrets("POSTGRES_PASSWORD=   # local only\n", lambda: "abc")
        assert out == "POSTGRES_PASSWORD=abc  # local only\n"

    def test_leaves_other_variables_alone(self) -> None:
        text = "GROQ_API_KEY=\nS3_ACCESS_KEY=minioadmin\nOTHER=\n"
        out, filled = init_env.fill_secrets(text, _counter())
        assert out == text
        assert filled == []

    def test_keys_that_must_differ_do_differ(self) -> None:
        out, _ = init_env.fill_secrets("PII_TOKENIZATION_KEY=\nPII_VAULT_ENCRYPTION_KEY=\n")
        first, second = (line.split("=", 1)[1] for line in out.splitlines())
        assert first and second and first != second

    def test_default_generator_is_long_and_random(self) -> None:
        a = init_env.fill_secrets("POSTGRES_PASSWORD=\n")[0]
        b = init_env.fill_secrets("POSTGRES_PASSWORD=\n")[0]
        assert a != b
        assert len(a.split("=", 1)[1].strip()) >= 48

    def test_windows_line_endings_survive(self) -> None:
        out, filled = init_env.fill_secrets("POSTGRES_PASSWORD=\r\nX=1\r\n", lambda: "pw")
        assert out == "POSTGRES_PASSWORD=pw\r\nX=1\r\n"
        assert filled == ["POSTGRES_PASSWORD"]

    def test_the_real_template_declares_every_generated_secret_empty(self) -> None:
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        _, filled = init_env.fill_secrets(template, _counter())
        assert set(filled) == set(init_env.GENERATED_SECRETS)


class TestMain:
    def _project(self, tmp_path: Path) -> Path:
        (tmp_path / ".env.example").write_text("S3_SECRET_KEY=\nGROQ_API_KEY=\n", encoding="utf-8")
        return tmp_path

    def test_creates_the_file_and_never_prints_a_secret(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = self._project(tmp_path)
        assert init_env.main(root) == 0
        content = (root / ".env").read_text(encoding="utf-8")
        secret = content.splitlines()[0].split("=", 1)[1]
        assert secret
        output = capsys.readouterr().out
        assert "generated S3_SECRET_KEY" in output
        assert secret not in output

    def test_second_run_changes_nothing(self, tmp_path: Path) -> None:
        root = self._project(tmp_path)
        init_env.main(root)
        first = (root / ".env").read_bytes()
        init_env.main(root)
        assert (root / ".env").read_bytes() == first

    def test_fills_a_secret_left_empty_in_an_existing_file(self, tmp_path: Path) -> None:
        root = self._project(tmp_path)
        (root / ".env").write_text("S3_SECRET_KEY=\nGROQ_API_KEY=mine\n", encoding="utf-8")
        init_env.main(root)
        lines = (root / ".env").read_text(encoding="utf-8").splitlines()
        assert lines[0].split("=", 1)[1]
        assert lines[1] == "GROQ_API_KEY=mine"

    def test_missing_template_is_an_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert init_env.main(tmp_path) == 2
        assert "not found" in capsys.readouterr().err
