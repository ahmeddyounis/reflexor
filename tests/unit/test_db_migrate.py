from __future__ import annotations

from pathlib import Path

import pytest

from reflexor.infra.db import migrate


def test_normalize_async_database_url_resolves_relative_sqlite_path_against_base_dir(
    tmp_path: Path,
) -> None:
    database_url, note, parsed_url = migrate._normalize_async_database_url(
        "sqlite:///./data/reflexor.db",
        base_dir=tmp_path,
    )

    assert parsed_url.get_backend_name() == "sqlite"
    assert parsed_url.get_driver_name() == "aiosqlite"
    assert parsed_url.database == str((tmp_path / "data" / "reflexor.db").resolve())
    assert database_url == f"sqlite+aiosqlite:///{parsed_url.database}"
    assert note is not None
    assert "Resolved SQLite database path" in note


def test_resolve_alembic_database_url_prefers_explicit_config_over_env(tmp_path: Path) -> None:
    ini_path = tmp_path / "alembic.ini"
    ini_path.write_text(
        "[alembic]\n"
        "script_location = alembic\n"
        "sqlalchemy.url = sqlite+aiosqlite:///./default.db\n",
        encoding="utf-8",
    )

    cfg = migrate.Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", "sqlite+aiosqlite:///./explicit.db")

    env_var_name = "REFLEXOR_DATABASE_URL"
    old_env = None
    if env_var_name in migrate.os.environ:
        old_env = migrate.os.environ[env_var_name]
    migrate.os.environ[env_var_name] = "sqlite+aiosqlite:///./env.db"
    try:
        resolved = migrate.resolve_alembic_database_url(cfg, base_dir=tmp_path)
    finally:
        if old_env is None:
            migrate.os.environ.pop(env_var_name, None)
        else:
            migrate.os.environ[env_var_name] = old_env

    assert resolved == f"sqlite+aiosqlite:///{(tmp_path / 'explicit.db').resolve()}"


def test_main_reset_dev_rejects_non_local_database_without_allow_remote(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(migrate, "_require_driver", lambda _url: None)

    exit_code = migrate.main(
        [
            "reset-dev",
            "--yes",
            "--database-url",
            "postgresql+asyncpg://user:pass@example.com:5432/reflexor",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "non-local database_url" in captured.err
