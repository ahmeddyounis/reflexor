from __future__ import annotations

import inspect
import warnings
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.security.scopes import ALL_SCOPES
from reflexor.tools.sdk import Tool, ToolContext, ToolManifest, ToolResult
from reflexor.tools.sdk.compat import SUPPORTED_TOOL_SDK_VERSIONS, is_supported_tool_sdk_version


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

    def load_entrypoints(self, *, settings: ReflexorSettings) -> int:
        """Discover and register tools from Python entry points.

        Entry points are loaded from the group `reflexor.tools`. Each entry point must resolve to
        either a Tool instance or a callable factory returning a Tool.

        Discovery is disabled by default and is only performed when
        `settings.enable_tool_entrypoints` is enabled.
        """

        if not settings.enable_tool_entrypoints:
            return 0

        from importlib import metadata

        loaded = 0
        for entrypoint in metadata.entry_points(group="reflexor.tools"):
            tool = _load_tool_entrypoint(entrypoint, settings=settings)
            tool = _validate_plugin_tool(tool, entrypoint_name=entrypoint.name, settings=settings)
            try:
                self.register(tool)
            except ValueError as exc:
                raise ValueError(
                    f"tool entrypoint {entrypoint.name!r} produced a duplicate tool name "
                    f"{tool.manifest.name!r}"
                ) from exc
            loaded += 1

        return loaded

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


class _EntrypointTool:
    def __init__(self, *, tool: Tool[BaseModel], manifest: ToolManifest) -> None:
        self._tool = tool
        self.manifest = manifest
        self.ArgsModel = tool.ArgsModel

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        return await self._tool.run(args, ctx)


def _load_tool_entrypoint(entrypoint: object, *, settings: ReflexorSettings) -> Tool[BaseModel]:
    name = getattr(entrypoint, "name", "<unknown>")
    load = getattr(entrypoint, "load", None)
    if load is None or not callable(load):
        raise ValueError(f"tool entrypoint {name!r} is missing a load() method")

    try:
        loaded_obj = load()
    except Exception as exc:
        raise ValueError(f"failed to import tool entrypoint {name!r}") from exc

    tool = _materialize_tool(loaded_obj, settings=settings, entrypoint_name=str(name))
    return tool


def _materialize_tool(
    loaded_obj: object, *, settings: ReflexorSettings, entrypoint_name: str
) -> Tool[BaseModel]:
    if _looks_like_tool(loaded_obj):
        return loaded_obj

    if callable(loaded_obj):
        factory = loaded_obj
        tool_obj = _call_factory(factory, settings=settings, entrypoint_name=entrypoint_name)
        if not _looks_like_tool(tool_obj):
            raise ValueError(
                f"tool entrypoint {entrypoint_name!r} factory did not return a Tool"
            )
        return tool_obj

    raise ValueError(
        f"tool entrypoint {entrypoint_name!r} must resolve to a Tool instance or a callable factory"
    )


def _call_factory(
    factory: object, *, settings: ReflexorSettings, entrypoint_name: str
) -> object:
    if not callable(factory):
        raise ValueError("factory must be callable")

    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        try:
            return factory()
        except Exception as exc:
            raise ValueError(
                f"tool entrypoint {entrypoint_name!r} factory raised an exception"
            ) from exc

    try:
        if "settings" in signature.parameters:
            return factory(settings=settings)
    except Exception as exc:
        raise ValueError(
            f"tool entrypoint {entrypoint_name!r} factory raised an exception"
        ) from exc

    required = [
        p
        for p in signature.parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if not required:
        try:
            return factory()
        except Exception as exc:
            raise ValueError(
                f"tool entrypoint {entrypoint_name!r} factory raised an exception"
            ) from exc

    if len(required) == 1:
        try:
            return factory(settings)
        except Exception as exc:
            raise ValueError(
                f"tool entrypoint {entrypoint_name!r} factory raised an exception"
            ) from exc

    raise ValueError(
        f"tool entrypoint {entrypoint_name!r} factory must accept no args or a single settings arg"
    )


def _looks_like_tool(obj: object) -> bool:
    if obj is None:
        return False
    if not hasattr(obj, "manifest") or not hasattr(obj, "ArgsModel") or not hasattr(obj, "run"):
        return False
    run = getattr(obj, "run", None)
    if run is None or not callable(run):
        return False
    return True


def _validate_plugin_tool(
    tool: Tool[BaseModel], *, entrypoint_name: str, settings: ReflexorSettings
) -> Tool[BaseModel]:
    raw_manifest = getattr(tool, "manifest", None)
    try:
        manifest = ToolManifest.model_validate(raw_manifest)
    except Exception as exc:
        raise ValueError(
            f"tool entrypoint {entrypoint_name!r} returned an invalid manifest"
        ) from exc

    if not is_supported_tool_sdk_version(manifest.sdk_version):
        supported = ", ".join(sorted(SUPPORTED_TOOL_SDK_VERSIONS))
        message = (
            f"tool entrypoint {entrypoint_name!r} requires unsupported sdk_version="
            f"{manifest.sdk_version!r} (supported: {supported})"
        )
        if settings.profile == "dev" and settings.allow_unsupported_tools:
            warnings.warn(message, category=UserWarning, stacklevel=2)
        else:
            raise ValueError(message)

    if manifest.permission_scope not in ALL_SCOPES:
        raise ValueError(
            f"tool entrypoint {entrypoint_name!r} has unknown permission_scope="
            f"{manifest.permission_scope!r}"
        )

    args_model = getattr(tool, "ArgsModel", None)
    if (
        args_model is None
        or not isinstance(args_model, type)
        or not issubclass(args_model, BaseModel)
    ):
        raise ValueError(
            f"tool entrypoint {entrypoint_name!r} has invalid ArgsModel (must extend BaseModel)"
        )

    run = getattr(tool, "run", None)
    if run is None or not callable(run) or not inspect.iscoroutinefunction(run):
        raise ValueError(f"tool entrypoint {entrypoint_name!r} run() must be async")

    return _EntrypointTool(tool=tool, manifest=manifest)
