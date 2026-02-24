"""Worker runner scaffolding.

This module is the entrypoint for a background worker process (not the CLI). It will be responsible
for wiring adapters (DB, queue backends), then driving the executor loop until shutdown.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class WorkerRunner:
    """Placeholder worker runner.

    Concrete runtime behavior will be implemented in later milestones.
    """

    def run(self) -> None:
        raise NotImplementedError("WorkerRunner is scaffolding only (not yet implemented).")
