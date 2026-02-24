from __future__ import annotations

from collections.abc import Callable

import pytest

from reflexor.security.scopes import Scope
from reflexor.tools.mock_tool import MockTool
from reflexor.tools.registry import ToolRegistry


@pytest.fixture()
def tool_registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture()
def register_mock_tool(tool_registry: ToolRegistry) -> Callable[..., MockTool]:
    def _register(
        name: str = "tests.mock",
        *,
        permission_scope: str = Scope.FS_READ.value,
        side_effects: bool = False,
        idempotent: bool = True,
    ) -> MockTool:
        tool = MockTool(
            tool_name=name,
            permission_scope=permission_scope,
            side_effects=side_effects,
            idempotent=idempotent,
        )
        tool_registry.register(tool)
        return tool

    return _register
