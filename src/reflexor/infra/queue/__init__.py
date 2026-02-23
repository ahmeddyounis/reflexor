"""Queue backends (infrastructure).

Concrete queue implementations belong to infrastructure so they can be swapped without changing
orchestrator/application logic.

Clean Architecture constraints:
- Backends may depend on the queue interface (`reflexor.orchestrator.queue`) and on inner layers.
- Orchestrator/application code must not import backends directly.
"""

from __future__ import annotations
