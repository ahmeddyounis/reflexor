from __future__ import annotations

import json
from pathlib import Path

import yaml

from reflexor.orchestrator.reflex_rules.models import ReflexRule

MAX_REFLEX_RULES_FILE_BYTES = 256_000


def _extract_rules(data: object) -> list[object]:
    raw_rules: object
    if isinstance(data, dict) and "rules" in data:
        raw_rules = data["rules"]
    else:
        raw_rules = data

    if not isinstance(raw_rules, list):
        raise ValueError("rules file must be a list or an object with a 'rules' list")
    return raw_rules


def _parse_rules(data: object) -> list[ReflexRule]:
    raw_rules = _extract_rules(data)

    rules: list[ReflexRule] = []
    for raw in raw_rules:
        rules.append(ReflexRule.model_validate(raw))
    return rules


def _read_rules_file(path: str | Path) -> str:
    resolved = Path(path)
    if not resolved.is_file():
        raise ValueError(f"reflex rules file not found or not a regular file: {resolved}")

    size_bytes = resolved.stat().st_size
    if size_bytes > MAX_REFLEX_RULES_FILE_BYTES:
        raise ValueError(
            f"reflex rules file is too large ({size_bytes} bytes); "
            f"max is {MAX_REFLEX_RULES_FILE_BYTES}"
        )

    return resolved.read_text(encoding="utf-8")


def load_reflex_rules_json(path: str | Path) -> list[ReflexRule]:
    try:
        data = json.loads(_read_rules_file(path))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid reflex rules JSON: {exc}") from exc
    return _parse_rules(data)


def load_reflex_rules_yaml(path: str | Path) -> list[ReflexRule]:
    try:
        data = yaml.safe_load(_read_rules_file(path))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid reflex rules YAML: {exc}") from exc
    return _parse_rules(data)


def load_reflex_rules(path: str | Path) -> list[ReflexRule]:
    resolved = Path(path)
    suffix = resolved.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return load_reflex_rules_yaml(resolved)
    return load_reflex_rules_json(resolved)


__all__ = ["load_reflex_rules", "load_reflex_rules_json", "load_reflex_rules_yaml"]
