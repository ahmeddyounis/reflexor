from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DomainError(Exception):
    """Base class for domain-level errors.

    The domain layer should raise typed exceptions with structured context to support
    consistent handling in outer layers (application/infra/interfaces).
    """

    message: str
    context: dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover
        if not self.context:
            return self.message
        return f"{self.message} (context={self.context})"


@dataclass(slots=True)
class SchemaViolation(DomainError):
    """Raised when input violates a schema/contract."""


@dataclass(slots=True)
class InvariantViolation(DomainError):
    """Raised when a domain invariant is violated."""


@dataclass(slots=True)
class InvalidTransition(DomainError):
    """Raised when attempting an invalid state transition."""

    current_state: str | None = None
    requested_state: str | None = None

    def __post_init__(self) -> None:
        merged = dict(self.context)
        if self.current_state is not None:
            merged.setdefault("current_state", self.current_state)
        if self.requested_state is not None:
            merged.setdefault("requested_state", self.requested_state)
        object.__setattr__(self, "context", merged)


@dataclass(slots=True)
class PolicyDenied(DomainError):
    """Raised when policy denies an action (placeholder; policy system TBD)."""

    permission_scope: str | None = None

    def __post_init__(self) -> None:
        merged = dict(self.context)
        if self.permission_scope is not None:
            merged.setdefault("permission_scope", self.permission_scope)
        object.__setattr__(self, "context", merged)


@dataclass(slots=True)
class BudgetExceeded(DomainError):
    """Raised when a run exceeds budget (time/tokens/cost) (placeholder)."""

    budget: str | None = None

    def __post_init__(self) -> None:
        merged = dict(self.context)
        if self.budget is not None:
            merged.setdefault("budget", self.budget)
        object.__setattr__(self, "context", merged)
