from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
import urllib.error
from pathlib import Path
from types import ModuleType

import pytest


def _load_pip_audit_gate_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "pip_audit_gate.py"
    spec = importlib.util.spec_from_file_location("pip_audit_gate_test_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_best_effort_score_ignores_non_finite_numeric_scores() -> None:
    module = _load_pip_audit_gate_module()

    score = module._best_effort_score_from_osv(  # pyright: ignore[reportAttributeAccessIssue]
        {
            "severity": [
                {"score": "NaN"},
                {"score": "Infinity"},
            ]
        }
    )

    assert score is None


def test_main_fails_closed_on_osv_lookup_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_pip_audit_gate_module()

    audit_path = tmp_path / "pip-audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "dependencies": [
                    {
                        "name": "demo",
                        "version": "1.0.0",
                        "vulns": [{"id": "GHSA-demo-1", "aliases": ["CVE-2026-0001"]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def _raise_lookup_error(vuln_id: str, *, timeout_s: float) -> dict[str, object]:
        raise urllib.error.URLError(f"lookup failed for {vuln_id} after {timeout_s}s")

    module._osv_get = _raise_lookup_error  # type: ignore[attr-defined]

    exit_code = module.main(["--audit-json", str(audit_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert '"lookup_failures": 1' in captured.out
    assert "OSV lookup errors prevented severity resolution" in captured.out
    assert "GHSA-demo-1" in captured.out


def test_main_reports_input_errors_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_pip_audit_gate_module()

    audit_path = tmp_path / "pip-audit.json"
    audit_path.write_text(json.dumps({"dependencies": []}), encoding="utf-8")
    allowlist_path = tmp_path / "allowlist.json"
    allowlist_path.write_text('{"bad": "shape"}', encoding="utf-8")

    exit_code = module.main(
        ["--audit-json", str(audit_path), "--allowlist", str(allowlist_path)]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "pip-audit gate input error:" in captured.err


def test_makefile_and_dev_extra_include_dependency_audit_tooling() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")

    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]

    assert "pip-audit==2.9.0" in dev_dependencies
    assert "audit-deps:" in makefile
    assert "$(MAKE) audit-deps" in makefile
