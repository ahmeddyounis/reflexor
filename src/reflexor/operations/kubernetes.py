from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ManifestIssue:
    path: Path
    message: str
    document_index: int


@dataclass(frozen=True, slots=True)
class _LoadedManifest:
    path: Path
    document_index: int
    document: dict[str, object]


def validate_manifest_tree(root: Path) -> list[ManifestIssue]:
    issues: list[ManifestIssue] = []
    manifests: list[_LoadedManifest] = []

    paths = [root] if root.is_file() else sorted(root.rglob("*.yaml"))
    for path in paths:
        if not path.is_file():
            continue
        try:
            raw_documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except OSError as exc:
            issues.append(
                ManifestIssue(
                    path=path,
                    message=f"failed to read manifest: {exc}",
                    document_index=0,
                )
            )
            continue
        except yaml.YAMLError as exc:
            issues.append(
                ManifestIssue(
                    path=path,
                    message=f"invalid YAML: {exc}",
                    document_index=0,
                )
            )
            continue
        if not raw_documents:
            issues.append(
                ManifestIssue(
                    path=path,
                    message="file does not contain any YAML documents",
                    document_index=0,
                )
            )
            continue

        for index, document in enumerate(raw_documents, start=1):
            if document is None:
                continue
            if not isinstance(document, dict):
                issues.append(
                    ManifestIssue(
                        path=path,
                        message="document must be a YAML mapping",
                        document_index=index,
                    )
                )
                continue
            manifests.append(_LoadedManifest(path=path, document_index=index, document=document))
            _validate_manifest(path, index, document, issues)
    _validate_manifest_set(manifests, issues)
    return issues


def _validate_manifest(
    path: Path,
    index: int,
    document: dict[str, object],
    issues: list[ManifestIssue],
) -> None:
    kind = document.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        issues.append(ManifestIssue(path=path, message="kind is required", document_index=index))
        return

    api_version = document.get("apiVersion")
    if not isinstance(api_version, str) or not api_version.strip():
        issues.append(
            ManifestIssue(path=path, message="apiVersion is required", document_index=index)
        )

    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        issues.append(
            ManifestIssue(path=path, message="metadata is required", document_index=index)
        )
        return
    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip():
        issues.append(
            ManifestIssue(path=path, message="metadata.name is required", document_index=index)
        )

    if kind in {"Deployment", "Job", "CronJob"}:
        _validate_workload(path, index, kind=kind, document=document, issues=issues)


def _validate_workload(
    path: Path,
    index: int,
    *,
    kind: str,
    document: dict[str, object],
    issues: list[ManifestIssue],
) -> None:
    spec = document.get("spec")
    if not isinstance(spec, dict):
        issues.append(ManifestIssue(path=path, message="spec is required", document_index=index))
        return

    if kind == "CronJob":
        job_template = spec.get("jobTemplate")
        if not isinstance(job_template, dict):
            issues.append(
                ManifestIssue(
                    path=path,
                    message="spec.jobTemplate is required",
                    document_index=index,
                )
            )
            return
        spec = job_template.get("spec")
        if not isinstance(spec, dict):
            issues.append(
                ManifestIssue(
                    path=path,
                    message="spec.jobTemplate.spec is required",
                    document_index=index,
                )
            )
            return

    template = spec.get("template")
    if not isinstance(template, dict):
        issues.append(
            ManifestIssue(path=path, message="spec.template is required", document_index=index)
        )
        return

    pod_spec = template.get("spec")
    if not isinstance(pod_spec, dict):
        issues.append(
            ManifestIssue(path=path, message="spec.template.spec is required", document_index=index)
        )
        return

    if pod_spec.get("automountServiceAccountToken") is not False:
        issues.append(
            ManifestIssue(
                path=path,
                message="spec.template.spec.automountServiceAccountToken must be false",
                document_index=index,
            )
        )

    pod_security_context = pod_spec.get("securityContext")
    if not isinstance(pod_security_context, dict):
        issues.append(
            ManifestIssue(
                path=path,
                message="spec.template.spec.securityContext is required",
                document_index=index,
            )
        )
    else:
        if pod_security_context.get("runAsNonRoot") is not True:
            issues.append(
                ManifestIssue(
                    path=path,
                    message="spec.template.spec.securityContext.runAsNonRoot must be true",
                    document_index=index,
                )
            )
        seccomp_profile = pod_security_context.get("seccompProfile")
        if not isinstance(seccomp_profile, dict) or seccomp_profile.get("type") != "RuntimeDefault":
            issues.append(
                ManifestIssue(
                    path=path,
                    message=(
                        "spec.template.spec.securityContext.seccompProfile.type "
                        "must be RuntimeDefault"
                    ),
                    document_index=index,
                )
            )

    containers = pod_spec.get("containers")
    if not isinstance(containers, list) or not containers:
        issues.append(
            ManifestIssue(
                path=path,
                message="spec.template.spec.containers is required",
                document_index=index,
            )
        )
        return

    for container in containers:
        if not isinstance(container, dict):
            issues.append(
                ManifestIssue(
                    path=path,
                    message="containers entries must be mappings",
                    document_index=index,
                )
            )
            continue
        resources = container.get("resources")
        if not isinstance(resources, dict):
            issues.append(
                ManifestIssue(
                    path=path,
                    message="containers must define resources",
                    document_index=index,
                )
            )
        else:
            requests = resources.get("requests")
            limits = resources.get("limits")
            if not isinstance(requests, dict) or not isinstance(limits, dict):
                issues.append(
                    ManifestIssue(
                        path=path,
                        message="containers must define resources.requests and resources.limits",
                        document_index=index,
                    )
                )
            else:
                for field_name, field_value in (
                    ("resources.requests.cpu", requests.get("cpu")),
                    ("resources.requests.memory", requests.get("memory")),
                    ("resources.limits.cpu", limits.get("cpu")),
                    ("resources.limits.memory", limits.get("memory")),
                ):
                    if not isinstance(field_value, str) or not field_value.strip():
                        issues.append(
                            ManifestIssue(
                                path=path,
                                message=f"containers must define {field_name}",
                                document_index=index,
                            )
                        )
        security_context = container.get("securityContext")
        if not isinstance(security_context, dict):
            issues.append(
                ManifestIssue(
                    path=path,
                    message="containers must define securityContext",
                    document_index=index,
                )
            )
            continue
        if security_context.get("allowPrivilegeEscalation") is not False:
            issues.append(
                ManifestIssue(
                    path=path,
                    message="allowPrivilegeEscalation must be false",
                    document_index=index,
                )
            )
        if security_context.get("readOnlyRootFilesystem") is not True:
            issues.append(
                ManifestIssue(
                    path=path,
                    message="readOnlyRootFilesystem should be true",
                    document_index=index,
                )
            )


def _validate_manifest_set(
    manifests: list[_LoadedManifest],
    issues: list[ManifestIssue],
) -> None:
    pvc_access_modes: dict[str, tuple[str, ...]] = {}
    pvc_paths: dict[str, Path] = {}
    pvc_indices: dict[str, int] = {}
    claim_consumers: dict[str, int] = {}

    for manifest in manifests:
        kind = manifest.document.get("kind")
        if kind == "PersistentVolumeClaim":
            metadata = manifest.document.get("metadata")
            spec = manifest.document.get("spec")
            if not isinstance(metadata, dict) or not isinstance(spec, dict):
                continue
            name = metadata.get("name")
            access_modes = spec.get("accessModes")
            if isinstance(name, str) and name.strip() and isinstance(access_modes, list):
                normalized = tuple(
                    mode.strip()
                    for mode in access_modes
                    if isinstance(mode, str) and mode.strip()
                )
                pvc_access_modes[name] = normalized
                pvc_paths[name] = manifest.path
                pvc_indices[name] = manifest.document_index
            continue

        pod_spec = _extract_pod_spec(manifest.document)
        if pod_spec is None:
            continue
        replica_count = _workload_replica_count(manifest.document)
        for claim_name in _claims_used_by_pod_spec(pod_spec):
            claim_consumers[claim_name] = claim_consumers.get(claim_name, 0) + replica_count

    for claim_name, consumer_count in claim_consumers.items():
        access_modes = pvc_access_modes.get(claim_name)
        if access_modes is None or consumer_count <= 1:
            continue
        if "ReadWriteMany" in access_modes:
            continue
        issues.append(
            ManifestIssue(
                path=pvc_paths[claim_name],
                document_index=pvc_indices[claim_name],
                message=(
                    f"persistentVolumeClaim {claim_name!r} is mounted by {consumer_count} "
                    "concurrent pods but does not allow ReadWriteMany"
                ),
            )
        )


def _extract_pod_spec(document: dict[str, object]) -> dict[str, Any] | None:
    spec = document.get("spec")
    if not isinstance(spec, dict):
        return None
    kind = document.get("kind")
    if kind == "CronJob":
        job_template = spec.get("jobTemplate")
        if not isinstance(job_template, dict):
            return None
        spec = job_template.get("spec")
        if not isinstance(spec, dict):
            return None
    template = spec.get("template")
    if not isinstance(template, dict):
        return None
    pod_spec = template.get("spec")
    if not isinstance(pod_spec, dict):
        return None
    return pod_spec


def _workload_replica_count(document: dict[str, object]) -> int:
    kind = document.get("kind")
    spec = document.get("spec")
    if kind == "Deployment" and isinstance(spec, dict):
        replicas = spec.get("replicas", 1)
        if isinstance(replicas, int) and replicas > 0:
            return replicas
    return 1


def _claims_used_by_pod_spec(pod_spec: dict[str, Any]) -> set[str]:
    volumes = pod_spec.get("volumes")
    if not isinstance(volumes, list):
        return set()

    mounted_volume_names: set[str] = set()
    containers = pod_spec.get("containers")
    if isinstance(containers, list):
        for container in containers:
            if not isinstance(container, dict):
                continue
            volume_mounts = container.get("volumeMounts")
            if not isinstance(volume_mounts, list):
                continue
            for mount in volume_mounts:
                if not isinstance(mount, dict):
                    continue
                name = mount.get("name")
                if isinstance(name, str) and name.strip():
                    mounted_volume_names.add(name)

    claims: set[str] = set()
    for volume in volumes:
        if not isinstance(volume, dict):
            continue
        volume_name = volume.get("name")
        if not isinstance(volume_name, str) or volume_name not in mounted_volume_names:
            continue
        pvc = volume.get("persistentVolumeClaim")
        if not isinstance(pvc, dict):
            continue
        claim_name = pvc.get("claimName")
        if isinstance(claim_name, str) and claim_name.strip():
            claims.add(claim_name)
    return claims


__all__ = ["ManifestIssue", "validate_manifest_tree"]
