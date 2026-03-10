from __future__ import annotations

import json
from pathlib import Path

import yaml

from reflexor.orchestrator.reflex_rules.models import ReflexRule


def _extract_rules(data: object) -> list[object]:
    raw_rules: object
    if isinstance(data, dict) and "rules" in data:
        raw_rules = data["rules"]
    else:
        raw_rules = data

    if not isinstance(raw_rules, list):
        raise ValueError("rules JSON must be a list or an object with a 'rules' list")
    return raw_rules


def _parse_rules(data: object) -> list[ReflexRule]:
    raw_rules = _extract_rules(data)

    rules: list[ReflexRule] = []
    for raw in raw_rules:
        rules.append(ReflexRule.model_validate(raw))
    return rules


def load_reflex_rules_json(path: str | Path) -> list[ReflexRule]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return _parse_rules(data)


def load_reflex_rules_yaml(path: str | Path) -> list[ReflexRule]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return _parse_rules(data)


def load_reflex_rules(path: str | Path) -> list[ReflexRule]:
    resolved = Path(path)
    suffix = resolved.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return load_reflex_rules_yaml(resolved)
    return load_reflex_rules_json(resolved)


__all__ = ["load_reflex_rules", "load_reflex_rules_json", "load_reflex_rules_yaml"]
