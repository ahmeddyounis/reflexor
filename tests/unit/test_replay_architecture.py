from __future__ import annotations

from pathlib import Path

from tests.architecture_utils import collect_forbidden_imports, iter_python_files


def test_replay_does_not_import_web_frameworks_or_runtime_shells() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    replay_root = src_root / "reflexor" / "replay"

    forbidden_prefixes = {
        "fastapi",
        "starlette",
        "reflexor.api",
        "reflexor.cli",
        "reflexor.worker",
    }

    offenders = collect_forbidden_imports(
        iter_python_files(replay_root),
        src_root=src_root,
        repo_root=repo_root,
        forbidden_prefixes=forbidden_prefixes,
    )

    assert not offenders, (
        "Forbidden imports detected in `reflexor.replay` (layering violation). "
        f"Offenders (file -> imports): {offenders}"
    )
