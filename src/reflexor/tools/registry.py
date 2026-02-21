from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from reflexor.tools.sdk import Tool, ToolManifest


class ToolRegistry:
    """In-memory registry for tool implementations.

    Tools are registered explicitly to keep the system open for extension and easy to test.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool by its manifest name.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """

        name = tool.manifest.name
        if name in self._tools:
            raise ValueError(f"tool already registered: {name!r}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        """Return the registered tool for `name`."""

        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name!r}") from exc

    def validate_exists(self, name: str) -> None:
        """Raise if a tool name is not registered."""

        if name not in self._tools:
            raise KeyError(f"unknown tool: {name!r}")

    def list_manifests(self) -> list[ToolManifest]:
        """Return manifests for all registered tools in registration order."""

        return [tool.manifest for tool in self._tools.values()]

    @contextmanager
    def override(self, name: str, tool: Tool) -> Iterator[None]:
        """Temporarily replace (or insert) a tool for tests."""

        if tool.manifest.name != name:
            raise ValueError(
                "override name must match tool.manifest.name "
                f"(got name={name!r}, manifest.name={tool.manifest.name!r})"
            )

        previous = self._tools.get(name)
        self._tools[name] = tool
        try:
            yield
        finally:
            if previous is None:
                self._tools.pop(name, None)
            else:
                self._tools[name] = previous
