from __future__ import annotations

import math
from uuid import uuid4

import pytest

from reflexor.memory.models import MemoryItem


def test_memory_item_rejects_non_finite_content_values() -> None:
    with pytest.raises(ValueError, match="content must be JSON-serializable"):
        MemoryItem(
            run_id=str(uuid4()),
            summary="summary",
            content={"delay_s": math.inf},
        )


def test_memory_item_rejects_inverted_timestamps() -> None:
    with pytest.raises(ValueError, match="updated_at_ms must be >= created_at_ms"):
        MemoryItem(
            run_id=str(uuid4()),
            summary="summary",
            created_at_ms=20,
            updated_at_ms=10,
        )
