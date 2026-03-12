from __future__ import annotations

import pytest

from reflexor.security.scopes import validate_scopes


def test_validate_scopes_normalizes_and_dedupes() -> None:
    assert validate_scopes([" fs.read ", "net.http", "fs.read"]) == ["fs.read", "net.http"]


def test_validate_scopes_rejects_blank_entries() -> None:
    with pytest.raises(ValueError, match="scope entries must be non-empty"):
        validate_scopes(["fs.read", "   "])
