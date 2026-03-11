from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class ManifestIssue:
    path: Path
    message: str
    document_index: int


def validate_manifest_tree(root: Path) -> list[ManifestIssue]:
    issues: list[ManifestIssue] = []
    for path in sorted(root.rglob("*.yaml")):
        if not path.is_file():
            continue
        raw_documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
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
            _validate_manifest(path, index, document, issues)
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


__all__ = ["ManifestIssue", "validate_manifest_tree"]
