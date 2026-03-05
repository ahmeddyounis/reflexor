from __future__ import annotations

import json
from pathlib import Path

from reflexor.orchestrator.reflex_rules.models import ReflexRule


def load_reflex_rules_json(path: str | Path) -> list[ReflexRule]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    raw_rules: object
    if isinstance(data, dict) and "rules" in data:
        raw_rules = data["rules"]
    else:
        raw_rules = data

    if not isinstance(raw_rules, list):
        raise ValueError("rules JSON must be a list or an object with a 'rules' list")

    rules: list[ReflexRule] = []
    for raw in raw_rules:
        rules.append(ReflexRule.model_validate(raw))
    return rules


__all__ = ["load_reflex_rules_json"]
