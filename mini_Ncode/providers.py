"""本地函数和 MCP 服务的工具适配层。"""
from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import AsyncExitStack
from typing import Any, Callable, Iterable
from .tool_types import ToolProvider, ToolResult, ToolSpec

logger = logging.getLogger(__name__)


class LocalToolProvider(ToolProvider):
    name = "local"

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, Callable]] = {}

    def tool(
        self,
        *,
        name: str | None = None,
        description: str = "",
        schema: dict | None = None,
        dangerous: bool = False,
        read_only: bool = False,
        timeout: float | None = None,
    ):
        def decorator(func: Callable):
            tool_name = name or func.__name__
            spec = ToolSpec(
                name=tool_name,
                description=description
                or (func.__doc__ or "").strip().split("\n")[0],
                input_schema=schema or {"type": "object", "properties": {}},
                metadata={
                    "source": "local", "dangerous": dangerous,
                    "read_only": read_only, "timeout": timeout,
                },
            )
            self.register(spec, func)
            return func
        return decorator

    def register(self, spec: ToolSpec, handler: Callable[..., Any]) -> None:
        if spec.name in self._tools:
            raise ValueError(f"本地工具重复注册: {spec.name}")
        if not callable(handler):
            raise TypeError(f"handler 必须可调用: {spec.name}")
        self._tools[spec.name] = (spec, handler)

    def register_many(
        self, specs: Iterable[ToolSpec], handlers: dict[str, Callable]
    ) -> None:
        for spec in specs:
            if spec.name not in handlers:
                raise KeyError(f"缺少 handler: {spec.name}")
            self.register(spec, handlers[spec.name])

    async def list_tools(self) -> list[ToolSpec]:
        return [spec for spec, _ in self._tools.values()]

    def get_spec(self, name: str) -> ToolSpec | None:
        entry = self._tools.get(name)
        return entry[0] if entry else None

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        entry = self._tools.get(name)
        if entry is None:
            return ToolResult(ok=False, error=f"未知本地工具: {name}")
        spec, handler = entry
        try:
            if inspect.iscoroutinefunction(handler):
                output = await handler(**arguments)
            else:
                output = await asyncio.to_thread(handler, **arguments)
            return ToolResult(ok=True, content=output, metadata={"source": "local"})
        except Exception as e:
            logger.exception("本地工具执行失败: %s", name)
            return ToolResult(ok=False, error=str(e), metadata={"source": "local"})


class MCPToolProvider(ToolProvider):
    def __init__(self, client: Any, server_name: str, prefix: bool = True):
        self._client = client
        self._stack = AsyncExitStack()
        self._entered = False
        self.server_name = server_name
        self.prefix = prefix
        self._specs: dict[str, ToolSpec] = {}
        self._raw_name: dict[str, str] = {}

    @property
    def name(self) -> str:
        return f"mcp:{self.server_name}"

    def _public_name(self, raw: str) -> str:
        return f"{self.server_name}_{raw}" if self.prefix else raw

    async def _ensure_entered(self) -> None:
        if not self._entered:
            await self._stack.enter_async_context(self._client)
            self._entered = True

    async def list_tools(self) -> list[ToolSpec]:
        await self._ensure_entered()
        tools = await self._client.list_tools()
        self._specs.clear()
        self._raw_name.clear()
        for t in tools:
            public = self._public_name(t.name)
            self._specs[public] = ToolSpec(
                name=public,
                description=t.description or "",
                input_schema=t.inputSchema or {"type": "object", "properties": {}},
                metadata={
                    "source": "mcp", "server": self.server_name,
                    "raw_name": t.name,
                },
            )
            self._raw_name[public] = t.name
        return list(self._specs.values())

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        await self._ensure_entered()
        raw = self._raw_name.get(name)
        if raw is None:
            return ToolResult(ok=False, error=f"未知 MCP 工具: {name}")
        try:
            result = await self._client.call_tool(raw, arguments)
        except Exception as e:
            logger.exception("MCP 工具执行失败: %s", name)
            return ToolResult(
                ok=False, error=str(e),
                metadata={"source": "mcp", "server": self.server_name},
            )
        is_error = bool(getattr(result, "isError", False))
        content = [
            c.model_dump() if hasattr(c, "model_dump") else c
            for c in (result.content or [])
        ]
        return ToolResult(
            ok=not is_error,
            content=content,
            error=content if is_error else None,
            metadata={"source": "mcp", "server": self.server_name},
        )

    async def close(self) -> None:
        try:
            await self._stack.aclose()
        except Exception:
            logger.exception("关闭 MCP provider 失败: %s", self.server_name)
        finally:
            self._entered = False
