"""Execution guards for tool-call hardening.

Guards provide a composable, DI-friendly pipeline to decide whether a tool call should be:
- allowed
- denied
- delayed
- or require human approval

Concrete tools and outer layers (API/worker) must not be imported here.
"""

from reflexor.guards.chain import GuardChain
from reflexor.guards.context import GuardContext
from reflexor.guards.decision import GuardAction, GuardDecision
from reflexor.guards.interface import ExecutionGuard
from reflexor.guards.policy_guard import PolicyGuard

__all__ = [
    "ExecutionGuard",
    "GuardAction",
    "GuardChain",
    "GuardContext",
    "GuardDecision",
    "PolicyGuard",
]
