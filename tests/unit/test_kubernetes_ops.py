from __future__ import annotations

from pathlib import Path

from reflexor.operations.kubernetes import validate_manifest_tree


def test_deploy_k8s_manifests_validate_cleanly() -> None:
    root = Path(__file__).resolve().parents[2] / "deploy" / "k8s"

    issues = validate_manifest_tree(root)

    assert issues == []
