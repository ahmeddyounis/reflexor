from __future__ import annotations

import pytest

from reflexor.orchestrator.event_suppression import DbEventSuppressor


def test_event_suppressor_rejects_non_finite_timing_values() -> None:
    with pytest.raises(ValueError, match="window_s must be finite and > 0"):
        DbEventSuppressor(
            uow_factory=lambda: None,  # type: ignore[return-value]
            repo=lambda _session: None,  # type: ignore[return-value]
            window_s=float("nan"),
        )

    with pytest.raises(ValueError, match="window_s must be finite and > 0"):
        DbEventSuppressor(
            uow_factory=lambda: None,  # type: ignore[return-value]
            repo=lambda _session: None,  # type: ignore[return-value]
            window_s=float("inf"),
        )

    with pytest.raises(ValueError, match="ttl_s must be finite and > 0"):
        DbEventSuppressor(
            uow_factory=lambda: None,  # type: ignore[return-value]
            repo=lambda _session: None,  # type: ignore[return-value]
            ttl_s=float("nan"),
        )

    with pytest.raises(ValueError, match="ttl_s must be finite and > 0"):
        DbEventSuppressor(
            uow_factory=lambda: None,  # type: ignore[return-value]
            repo=lambda _session: None,  # type: ignore[return-value]
            ttl_s=float("inf"),
        )
