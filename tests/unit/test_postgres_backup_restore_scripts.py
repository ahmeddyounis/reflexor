from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run_script(
    script_name: str,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    command = [sys.executable, str(repo_root / "scripts" / script_name), *args]
    combined_env = os.environ.copy()
    if env is not None:
        combined_env.update(env)
    if env is None or "REFLEXOR_PROFILE" not in env:
        combined_env["REFLEXOR_PROFILE"] = "dev"
    return subprocess.run(
        command,
        cwd=repo_root,
        env=combined_env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_postgres_backup_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    output_path = tmp_path / "reflexor.dump"
    output_path.write_text("existing", encoding="utf-8")

    completed = _run_script(
        "postgres_backup.py",
        "--database-url",
        "postgresql+asyncpg://user:pass@localhost:5432/reflexor",
        "--output",
        str(output_path),
    )

    assert completed.returncode == 2
    assert "output file already exists" in completed.stderr


def test_postgres_restore_rejects_remote_target_without_override(tmp_path: Path) -> None:
    input_path = tmp_path / "reflexor.dump"
    input_path.write_text("placeholder", encoding="utf-8")

    completed = _run_script(
        "postgres_restore.py",
        "--database-url",
        "postgresql+asyncpg://user:pass@db.example.test:5432/reflexor",
        "--input",
        str(input_path),
        "--format",
        "custom",
        "--yes",
    )

    assert completed.returncode == 2
    assert "non-local database_url" in completed.stderr


def test_postgres_restore_rejects_prod_profile_without_override(tmp_path: Path) -> None:
    input_path = tmp_path / "reflexor.dump"
    input_path.write_text("placeholder", encoding="utf-8")

    completed = _run_script(
        "postgres_restore.py",
        "--database-url",
        "postgresql+asyncpg://user:pass@localhost:5432/reflexor",
        "--input",
        str(input_path),
        "--format",
        "custom",
        "--yes",
        env={"REFLEXOR_PROFILE": "prod"},
    )

    assert completed.returncode == 2
    assert "REFLEXOR_PROFILE=prod" in completed.stderr


def test_postgres_backup_reports_missing_pg_dump(tmp_path: Path) -> None:
    output_path = tmp_path / "reflexor.dump"

    completed = _run_script(
        "postgres_backup.py",
        "--database-url",
        "postgresql+asyncpg://user:pass@localhost:5432/reflexor",
        "--output",
        str(output_path),
        env={"PATH": ""},
    )

    assert completed.returncode == 1
    assert "pg_dump is not installed or not on PATH" in completed.stderr
