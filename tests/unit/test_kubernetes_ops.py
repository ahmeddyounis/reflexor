from __future__ import annotations

from pathlib import Path

from reflexor.operations.kubernetes import validate_manifest_tree


def test_deploy_k8s_manifests_validate_cleanly() -> None:
    root = Path(__file__).resolve().parents[2] / "deploy" / "k8s"

    issues = validate_manifest_tree(root)

    assert issues == []


def test_validate_manifest_tree_reports_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata: [", encoding="utf-8")

    issues = validate_manifest_tree(path)

    assert len(issues) == 1
    assert issues[0].path == path
    assert issues[0].document_index == 0
    assert "invalid YAML" in issues[0].message


def test_validate_manifest_tree_reports_missing_pod_hardening(tmp_path: Path) -> None:
    path = tmp_path / "deployment.yaml"
    path.write_text(
        """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: reflexor-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: reflexor
  template:
    metadata:
      labels:
        app: reflexor
    spec:
      containers:
        - name: reflexor
          image: reflexor:1.0.0
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
""".strip(),
        encoding="utf-8",
    )

    issues = validate_manifest_tree(path)
    messages = {issue.message for issue in issues}

    assert "spec.template.spec.automountServiceAccountToken must be false" in messages
    assert "spec.template.spec.securityContext is required" in messages
    assert "containers must define resources" in messages


def test_validate_manifest_tree_reports_shared_rwo_claim(tmp_path: Path) -> None:
    (tmp_path / "workspace-pvc.yaml").write_text(
        """
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: reflexor-workspace
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "worker-deployment.yaml").write_text(
        """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: reflexor-worker
spec:
  replicas: 2
  selector:
    matchLabels:
      app: reflexor
  template:
    metadata:
      labels:
        app: reflexor
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: reflexor
          image: reflexor:1.0.0
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 256Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
          volumeMounts:
            - name: workspace
              mountPath: /workspace
      volumes:
        - name: workspace
          persistentVolumeClaim:
            claimName: reflexor-workspace
""".strip(),
        encoding="utf-8",
    )

    issues = validate_manifest_tree(tmp_path)

    assert any(
        "does not allow ReadWriteMany" in issue.message
        for issue in issues
    )
