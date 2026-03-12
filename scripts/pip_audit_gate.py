from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Finding:
    dependency: str
    dependency_version: str | None
    vuln_id: str
    aliases: tuple[str, ...]
    score: float | None
    severity: str
    osv_id_used: str | None


@dataclass(frozen=True, slots=True)
class LookupFailure:
    dependency: str
    dependency_version: str | None
    vuln_id: str
    attempted_ids: tuple[str, ...]
    errors: tuple[str, ...]


_SEVERITY_ORDER = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
    "unknown": 99,
    "ignored": -1,
}


def _exception_type_label(exc: BaseException) -> str:
    return type(exc).__name__


def _parse_positive_finite_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise argparse.ArgumentTypeError("must be a finite value greater than 0")
    return parsed


def _read_allowlist(path: Path | None) -> set[str]:
    if path is None:
        return set()
    if not path.exists():
        return set()

    raw = path.read_text(encoding="utf-8")
    stripped = raw.strip()
    if not stripped:
        return set()

    # Allow JSON list (useful for machine edits), otherwise parse as newline-delimited text.
    if stripped.startswith(("[", "{")):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError("allowlist JSON must be a list[str]")
        return {item.strip() for item in parsed if item.strip()}

    ids: set[str] = set()
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        ids.add(text)
    return ids


def _parse_pip_audit_json(path: Path) -> list[object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    deps = payload.get("dependencies")
    if not isinstance(deps, list):
        raise ValueError("pip-audit json must contain a 'dependencies' list")
    return deps


def _osv_get(vuln_id: str, *, timeout_s: float) -> dict[str, Any]:
    quoted = urllib.parse.quote(vuln_id, safe="")
    url = f"https://api.osv.dev/v1/vulns/{quoted}"
    req = urllib.request.Request(url=url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = resp.read()
    parsed = json.loads(data.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("unexpected OSV response")
    return parsed


def _roundup_1_decimal(value: float) -> float:
    # CVSS v3 rounding: round up to the nearest 0.1.
    return math.ceil(value * 10.0 - 1e-9) / 10.0


def _cvss_v3_base_score(vector: str) -> float:
    text = vector.strip()
    if not (text.startswith("CVSS:3.0/") or text.startswith("CVSS:3.1/")):
        raise ValueError("unsupported CVSS v3 vector")

    parts = text.split("/")
    metrics: dict[str, str] = {}
    for item in parts[1:]:
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        metrics[key] = value

    required = {"AV", "AC", "PR", "UI", "S", "C", "I", "A"}
    if not required.issubset(metrics):
        missing = sorted(required - set(metrics))
        raise ValueError(f"missing CVSS metrics: {missing}")

    av = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.20}[metrics["AV"]]
    ac = {"L": 0.77, "H": 0.44}[metrics["AC"]]
    ui = {"N": 0.85, "R": 0.62}[metrics["UI"]]

    scope = metrics["S"]
    if scope not in {"U", "C"}:
        raise ValueError("invalid scope")

    pr_value = metrics["PR"]
    if pr_value == "N":
        pr = 0.85
    elif pr_value == "L":
        pr = 0.62 if scope == "U" else 0.68
    elif pr_value == "H":
        pr = 0.27 if scope == "U" else 0.50
    else:
        raise ValueError("invalid privileges required")

    c = {"H": 0.56, "L": 0.22, "N": 0.00}[metrics["C"]]
    i = {"H": 0.56, "L": 0.22, "N": 0.00}[metrics["I"]]
    a = {"H": 0.56, "L": 0.22, "N": 0.00}[metrics["A"]]

    iss = 1.0 - ((1.0 - c) * (1.0 - i) * (1.0 - a))

    if scope == "U":
        impact = 6.42 * iss
    else:
        impact = 7.52 * (iss - 0.029) - 3.25 * ((iss - 0.02) ** 15.0)

    exploitability = 8.22 * av * ac * pr * ui

    if impact <= 0:
        return 0.0

    if scope == "U":
        score = min(impact + exploitability, 10.0)
    else:
        score = min(1.08 * (impact + exploitability), 10.0)

    return _roundup_1_decimal(score)


def _score_to_severity(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0.0:
        return "low"
    return "none"


def _best_effort_score_from_osv(vuln: Mapping[str, Any]) -> float | None:
    # Prefer explicit OSV severity entries when present.
    entries = vuln.get("severity")
    best: float | None = None
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            score = entry.get("score")
            if not isinstance(score, str):
                continue
            score_text = score.strip()
            if not score_text:
                continue
            try:
                if score_text.startswith("CVSS:3."):
                    candidate = _cvss_v3_base_score(score_text)
                else:
                    candidate = float(score_text)
            except Exception:
                continue
            if not math.isfinite(candidate) or candidate < 0.0:
                continue
            best = candidate if best is None else max(best, candidate)

    if best is not None:
        return best

    # Fallback: some entries include a coarse database-specific severity string.
    db = vuln.get("database_specific")
    if isinstance(db, dict):
        sev = db.get("severity")
        if isinstance(sev, str):
            normalized = sev.strip().lower()
            mapping = {"critical": 9.0, "high": 7.0, "medium": 4.0, "moderate": 4.0, "low": 0.1}
            if normalized in mapping:
                return mapping[normalized]

    return None


def _severity_meets_threshold(severity: str, *, min_severity: str) -> bool:
    if severity not in _SEVERITY_ORDER:
        return False
    if min_severity not in _SEVERITY_ORDER:
        raise ValueError(f"invalid min_severity: {min_severity}")
    return _SEVERITY_ORDER[severity] >= _SEVERITY_ORDER[min_severity]


def _collect_findings(
    deps: list[object],
    *,
    allowlist: set[str],
    min_severity: str,
    fail_on_unknown: bool,
    timeout_s: float,
) -> tuple[list[Finding], list[Finding], list[Finding], list[LookupFailure]]:
    ignored: list[Finding] = []
    unknown: list[Finding] = []
    failing: list[Finding] = []
    lookup_failures: list[LookupFailure] = []

    osv_cache: dict[str, dict[str, Any] | None] = {}
    osv_errors: dict[str, str] = {}

    for dep in deps:
        if not isinstance(dep, dict):
            continue
        name = dep.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        dep_version = dep.get("version")
        dep_version_str = dep_version if isinstance(dep_version, str) else None

        vulns = dep.get("vulns", [])
        if not isinstance(vulns, list):
            continue

        for vuln in vulns:
            if not isinstance(vuln, dict):
                continue
            vuln_id = vuln.get("id")
            if not isinstance(vuln_id, str) or not vuln_id.strip():
                continue

            aliases_field = vuln.get("aliases", [])
            aliases: tuple[str, ...] = ()
            if isinstance(aliases_field, list) and all(
                isinstance(item, str) for item in aliases_field
            ):
                aliases = tuple(item for item in aliases_field if item.strip())

            ids_for_allow = {vuln_id, *aliases}
            if ids_for_allow & allowlist:
                ignored.append(
                    Finding(
                        dependency=name,
                        dependency_version=dep_version_str,
                        vuln_id=vuln_id,
                        aliases=aliases,
                        score=None,
                        severity="ignored",
                        osv_id_used=None,
                    )
                )
                continue

            candidates = [vuln_id, *aliases]
            best_score: float | None = None
            best_osv_id: str | None = None
            candidate_errors: list[str] = []

            for candidate_id in candidates:
                cached = osv_cache.get(candidate_id, ...)
                if cached is ...:
                    try:
                        osv_cache[candidate_id] = _osv_get(candidate_id, timeout_s=timeout_s)
                    except urllib.error.HTTPError as exc:
                        if exc.code == 404:
                            osv_cache[candidate_id] = None
                        else:
                            osv_errors[candidate_id] = f"HTTP {exc.code}"
                            osv_cache[candidate_id] = None
                    except urllib.error.URLError as exc:
                        reason = exc.reason
                        osv_errors[candidate_id] = (
                            str(reason) if isinstance(reason, str) else repr(reason)
                        )
                        osv_cache[candidate_id] = None
                    except TimeoutError:
                        osv_errors[candidate_id] = "timed out"
                        osv_cache[candidate_id] = None
                    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                        osv_errors[candidate_id] = str(exc)
                        osv_cache[candidate_id] = None
                    except Exception as exc:
                        osv_errors[candidate_id] = _exception_type_label(exc)
                        osv_cache[candidate_id] = None
                osv_payload = osv_cache.get(candidate_id)
                cached_error = osv_errors.get(candidate_id)
                if cached_error is not None:
                    candidate_errors.append(f"{candidate_id}: {cached_error}")
                if not isinstance(osv_payload, dict):
                    continue

                score = _best_effort_score_from_osv(osv_payload)
                if score is None:
                    continue
                if best_score is None or score > best_score:
                    best_score = score
                    best_osv_id = candidate_id

            if best_score is None and candidate_errors:
                lookup_failures.append(
                    LookupFailure(
                        dependency=name,
                        dependency_version=dep_version_str,
                        vuln_id=vuln_id,
                        attempted_ids=tuple(candidates),
                        errors=tuple(candidate_errors),
                    )
                )
                continue

            severity = _score_to_severity(best_score)

            finding = Finding(
                dependency=name,
                dependency_version=dep_version_str,
                vuln_id=vuln_id,
                aliases=aliases,
                score=best_score,
                severity=severity,
                osv_id_used=best_osv_id,
            )

            if severity == "unknown":
                unknown.append(finding)
                if not fail_on_unknown:
                    continue

            if _severity_meets_threshold(severity, min_severity=min_severity):
                failing.append(finding)

    failing.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.dependency, f.vuln_id))
    unknown.sort(key=lambda f: (f.dependency, f.vuln_id))
    lookup_failures.sort(key=lambda f: (f.dependency, f.vuln_id))
    return ignored, unknown, failing, lookup_failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate pip-audit results by severity.")
    parser.add_argument("--audit-json", required=True, help="Path to pip-audit JSON output.")
    parser.add_argument(
        "--allowlist",
        default=".github/pip-audit-allowlist.txt",
        help="Path to allowlist file (IDs to ignore).",
    )
    parser.add_argument(
        "--min-severity",
        default="high",
        choices=["low", "medium", "high", "critical"],
        help="Minimum severity that fails the gate.",
    )
    parser.add_argument(
        "--fail-on-unknown",
        action="store_true",
        help="Fail the gate if severity cannot be determined.",
    )
    parser.add_argument(
        "--osv-timeout-s",
        type=_parse_positive_finite_float,
        default=15.0,
        help="OSV lookup timeout in seconds (finite and > 0).",
    )
    args = parser.parse_args(argv)

    audit_path = Path(args.audit_json)
    allowlist_path = Path(args.allowlist) if args.allowlist else None

    try:
        allowlist = _read_allowlist(allowlist_path)
        deps = _parse_pip_audit_json(audit_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"pip-audit gate input error: {exc}", file=sys.stderr)
        return 2

    started = time.perf_counter()
    ignored, unknown, failing, lookup_failures = _collect_findings(
        deps,
        allowlist=allowlist,
        min_severity=str(args.min_severity),
        fail_on_unknown=bool(args.fail_on_unknown),
        timeout_s=float(args.osv_timeout_s),
    )
    elapsed_s = time.perf_counter() - started

    total_vulns = sum(
        len(dep.get("vulns", []))
        for dep in deps
        if isinstance(dep, dict) and isinstance(dep.get("vulns"), list)
    )

    print(
        json.dumps(
            {
                "total_vulns": int(total_vulns),
                "ignored": int(len(ignored)),
                "unknown": int(len(unknown)),
                "failing": int(len(failing)),
                "lookup_failures": int(len(lookup_failures)),
                "min_severity": str(args.min_severity),
                "fail_on_unknown": bool(args.fail_on_unknown),
                "elapsed_s": round(float(elapsed_s), 3),
            },
            sort_keys=True,
        )
    )

    if unknown and not args.fail_on_unknown:
        print("pip-audit gate warning (unknown severity; investigate and/or allowlist):")
        for finding in unknown[:50]:
            version = f"=={finding.dependency_version}" if finding.dependency_version else ""
            osv_used = "" if finding.osv_id_used is None else f" (osv_id={finding.osv_id_used})"
            print(f"- {finding.dependency}{version}: {finding.vuln_id} severity=unknown{osv_used}")
        if len(unknown) > 50:
            print(f"... and {len(unknown) - 50} more")

    if lookup_failures:
        print("pip-audit gate failed (OSV lookup errors prevented severity resolution):")
        for failure in lookup_failures[:50]:
            version = f"=={failure.dependency_version}" if failure.dependency_version else ""
            errors = "; ".join(failure.errors)
            print(f"- {failure.dependency}{version}: {failure.vuln_id} lookup_errors={errors}")
        if len(lookup_failures) > 50:
            print(f"... and {len(lookup_failures) - 50} more")
        return 1

    if not failing:
        return 0

    print("pip-audit gate failed (vulnerabilities above threshold detected):")
    for finding in failing[:50]:
        version = f"=={finding.dependency_version}" if finding.dependency_version else ""
        score = "unknown" if finding.score is None else f"{finding.score:.1f}"
        osv_used = "" if finding.osv_id_used is None else f" (osv_id={finding.osv_id_used})"
        print(
            f"- {finding.dependency}{version}: {finding.vuln_id} "
            f"severity={finding.severity} score={score}{osv_used}"
        )
    if len(failing) > 50:
        print(f"... and {len(failing) - 50} more")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
