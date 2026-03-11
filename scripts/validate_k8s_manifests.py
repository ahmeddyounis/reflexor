from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reflexor.operations.kubernetes import validate_manifest_tree


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Kubernetes YAML manifests.")
    parser.add_argument(
        "path",
        nargs="?",
        default="deploy/k8s",
        help="Root directory containing Kubernetes YAML manifests.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        print(f"ERROR: path does not exist: {root}", file=sys.stderr)
        return 2

    issues = validate_manifest_tree(root)
    if issues:
        for issue in issues:
            print(
                f"{issue.path}:{issue.document_index}: {issue.message}",
                file=sys.stderr,
            )
        return 1

    print(f"OK: validated Kubernetes manifests under {root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
