"""工具定义、统一结果与 Provider 接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    metadata: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    ok: bool
    content: Any = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "content": self.content,
            "error": self.error, "metadata": self.metadata,
        }


class ToolProvider(ABC):
    name: str = "provider"

    @abstractmethod
    async def list_tools(self) -> list[ToolSpec]: ...

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict) -> ToolResult: ...

    async def close(self) -> None:
        pass
