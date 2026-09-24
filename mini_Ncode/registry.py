"""聚合工具定义，并把调用路由到对应 Provider。"""
from __future__ import annotations

import asyncio
import logging
from .tool_types import ToolProvider, ToolResult, ToolSpec

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._providers: list[ToolProvider] = []
        self._specs: dict[str, ToolSpec] = {}
        self._owner: dict[str, ToolProvider] = {}
        self._lock = asyncio.Lock()

    def add_provider(self, provider: ToolProvider) -> None:
        self._providers.append(provider)

    async def refresh(self) -> None:
        async with self._lock:
            specs: dict[str, ToolSpec] = {}
            owner: dict[str, ToolProvider] = {}
            for p in self._providers:
                for spec in await p.list_tools():
                    if spec.name in specs:
                        raise ValueError(
                            f"工具名冲突: {spec.name} "
                            f"({owner[spec.name].name} vs {p.name})"
                        )
                    specs[spec.name] = spec
                    owner[spec.name] = p
            self._specs = specs
            self._owner = owner

    def to_llm(self) -> list[dict]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for spec in self._specs.values()
        ]

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    async def invoke(self, name: str, arguments: dict) -> ToolResult:
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(ok=False, error=f"未知工具: {name}")
        provider = self._owner[name]
        return await provider.call_tool(name, arguments)

    async def close(self) -> None:
        for p in self._providers:
            try:
                await p.close()
            except Exception:
                logger.exception("关闭 provider 失败: %s", p.name)
