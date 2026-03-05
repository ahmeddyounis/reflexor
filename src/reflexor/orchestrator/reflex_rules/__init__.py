"""Rule-based reflex routing with safe templating.

Reflex rules are evaluated in order and can quickly decide whether to:
- emit a small set of "fast tasks" (tool calls) without invoking the planner,
- request planning, or
- drop the event.

Templating:
Args templates support placeholder substitution for strings like `${payload.url}` and
`${event.type}` using strict dot-lookup only. Placeholders are validated at rule-load time to
reject unsafe/unknown syntax (no eval, no bracket indexing, no function calls). At runtime,
missing keys raise a template resolution error.

Clean Architecture:
- Orchestrator is application-layer code.
- This package may depend on `reflexor.domain` and other orchestrator contracts.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from reflexor.orchestrator.reflex_rules.loader import load_reflex_rules_json
from reflexor.orchestrator.reflex_rules.models import (
    DropAction,
    FastToolAction,
    NeedsPlanningAction,
    ReflexRule,
    ReflexRuleMatch,
)
from reflexor.orchestrator.reflex_rules.router import RuleBasedReflexRouter
from reflexor.orchestrator.reflex_rules.template import (
    ReflexTemplateError,
    TemplateResolutionError,
    TemplateValidationError,
    render_template_value,
)

__all__ = [
    "DropAction",
    "FastToolAction",
    "NeedsPlanningAction",
    "ReflexRule",
    "ReflexRuleMatch",
    "RuleBasedReflexRouter",
    "ReflexTemplateError",
    "TemplateResolutionError",
    "TemplateValidationError",
    "load_reflex_rules_json",
    "render_template_value",
]
