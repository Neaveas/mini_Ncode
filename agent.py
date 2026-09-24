from __future__ import annotations
import os
import re
import subprocess
from pathlib import Path
import yaml

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional
from anthropic import Anthropic
from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from logging.handlers import RotatingFileHandler

def setup_logger() -> None:
    log_dir = Path('.logs')
    log_dir.mkdir(exist_ok=True)
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s')
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(log_dir / 'agent.log', maxBytes=5*1024*1024, backupCount=5,encoding="utf-8",)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[console, file_handler],
    )

logger = logging.getLogger(__name__)

load_dotenv()
WORKDIR  =   Path.cwd()

SYSTEM_PROMPT = f"You are a coding agent at {WORKDIR}. All destructive operations require user approval."

class Skillloader:
    def __init__(self,skill_dir:Path):
        self.skill_dir = skill_dir
        self.skills :dict[str,dict[str,str]] = {}
        self.scan()

    @staticmethod
    def parse_frontmatter(text:str) -> tuple[dict,str]:
        """返回一个元组，包含解析后的 frontmatter 字典和剩余的文本内容"""
        lines = text.splitlines(keepends =True)
        if not lines or lines[0].strip("\r\n") != "---":
            return {}, text
        close_index = next((index for index, line in enumerate(lines[1:], start=1) if line.strip("\r\n") == "---"),None)
        if close_index is None:
            return {}, text

        frontmatter = ''.join(lines[1:close_index])
        body = ''.join(lines[close_index + 1:])
        try:
            metadata =  yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return metadata, body

    def scan(self):
        """扫描技能目录，加载技能到skills字典中"""
        self.skills.clear()
        if not self.skill_dir.exists():
            return
        skill_root = self.skill_dir.resolve()
        for manifest in sorted(self.skill_dir.glob("*/SKILL.md")):
            if (not manifest.is_file() or
                not manifest.parent.is_dir() or
                not manifest.parent.resolve().is_relative_to(skill_root)
            ):
                continue
            content = manifest.read_text(encoding="utf-8")
            metadata, body = self.parse_frontmatter(content)
            raw_name = metadata.get("name")
            name = raw_name.strip() if isinstance(raw_name, str) else ''
            name = name or manifest.parent.name
            raw_description = metadata.get("description")
            description = raw_description if isinstance(raw_description, str) else ''
            #如果没有提供描述，则使用正文的第一行作为描述
            description = description or body.split('\n',1)[0]
            description = ''.join(str(description).lstrip('#').strip())
            self.skills[name] = {
                'name' : name,
                'description' : description,
                'content' : body,
            }

    def catalog(self) -> str:
        """返回有哪些技能"""
        if not self.skills:
            return "No skills found."
        return '\n'.join(f"- {skill['name']}: {skill['description']}" for skill in self.skills.values())

    def load(self, skill_name:str) -> str:
        """根据技能名称加载技能"""
        skill =self.skills.get(skill_name)
        if skill :
            return skill['content']
        available_skills = ', '.join(self.skills.keys())
        return f"Error: Skill '{skill_name}' not found. Available skills: {available_skills}"

    

# ============================================================
# 1. ToolSpec：工具的纯定义
# ============================================================

@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    metadata: dict = field(default_factory=dict)


# ============================================================
# 2. ToolResult：统一返回结构
# ============================================================


@dataclass
class ToolResult:
    ok: bool
    content: Any = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "content": self.content,
            "error": self.error,
            "metadata": self.metadata,
        }

# ============================================================
# 3. ToolProvider：工具来源抽象
# ============================================================

class ToolProvider(ABC):
    name: str = "provider"

    @abstractmethod
    async def list_tools(self) -> list[ToolSpec]:
        ...

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        ...

    async def close(self) -> None:
        pass

# ============================================================
# 4. LocalToolProvider：本地 Python 函数
# ============================================================

class LocalToolProvider(ToolProvider):
    name = "local"

    def __init__(self):
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
                    description=description or (func.__doc__ or "").strip().split("\n")[0],
                    input_schema=schema or {"type": "object", "properties": {}},
                    metadata={
                        "source": "local",
                        "dangerous": dangerous,
                        "read_only": read_only,
                        "timeout": timeout,
                    },
                )
                self.register(spec, func)
                return func
            return decorator

    def register(
        self,
        spec: ToolSpec,
        handler: Callable[..., Any],
    ) -> None:
        if spec.name in self._tools:
            raise ValueError(f"本地工具重复注册: {spec.name}")
        if not callable(handler):
            raise TypeError(f"handler 必须可调用: {spec.name}")
        self._tools[spec.name] = (spec, handler)

    def register_many(
        self,
        specs: Iterable[ToolSpec],
        handlers: dict[str, Callable],
    ) -> None:
        for spec in specs:
            if spec.name not in handlers:
                raise KeyError(f"缺少 handler: {spec.name}")
            self.register(spec, handlers[spec.name])

    async def list_tools(self) -> list[ToolSpec]:
        return [spec for spec, _ in self._tools.values()]

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        entry = self._tools.get(name)
        if entry is None:
            return ToolResult(ok=False, error=f"未知本地工具: {name}")
        spec, handler = entry
        try:
            if inspect.iscoroutinefunction(handler):
                output = await handler(**arguments)
            else:
                output = await asyncio.to_thread(handler, **arguments)  # 同步函数丢线程
            return ToolResult(ok=True, content=output,
                              metadata={"source": "local"})
        except Exception as e:
            logger.exception("本地工具执行失败: %s", name)
            return ToolResult(ok=False, error=str(e),
                              metadata={"source": "local"})

# 5. MCPToolProvider：MCP 服务
# ============================================================

# class MCPToolProvider(ToolProvider):
#     """
#     包装一个已连接的 MCP ClientSession。

#     用法:
#         session = await connect_mcp(...)   # mcp.ClientSession
#         provider = MCPToolProvider(session, server_name="fs")
#         registry.add_provider(provider)
#     """


#     def __init__(self, session, server_name: str, prefix: bool = True):
#         self.session = session
#         self.server_name = server_name
#         # 是否给工具名加 server_name. 前缀。
#         self.prefix = prefix
#         # 对外名 → ToolSpec，list_tools 的缓存。
#         self._specs: dict[str, ToolSpec] = {}
#         # 对外名 → MCP 原始工具名，调用时要用原始名
#         self._raw_name: dict[str, str] = {}

#     @property
#     def name(self) -> str:
#         return f"mcp:{self.server_name}"

#     def _public_name(self, raw: str) -> str:
#         return f"{self.server_name}.{raw}" if self.prefix else raw

#     async def list_tools(self) -> list[ToolSpec]:
#         resp = await self.session.list_tools()
#         self._specs.clear()
#         self._raw_name.clear()
#         for t in resp.tools:
#             public = self._public_name(t.name)
#             spec = ToolSpec(
#                 name=public,
#                 description=t.description or "",
#                 input_schema=t.inputSchema or {"type": "object", "properties": {}},
#                 metadata={
#                     "source": "mcp",
#                     "server": self.server_name,
#                     "raw_name": t.name,
#                 },
#             )
#             self._specs[public] = spec
#             self._raw_name[public] = t.name
#         return list(self._specs.values())

#     async def call_tool(self, name: str, arguments: dict) -> ToolResult:
#         raw = self._raw_name.get(name)
#         if raw is None:
#             return ToolResult(ok=False, error=f"未知 MCP 工具: {name}")
#         try:
#             result = await self.session.call_tool(raw, arguments)
#             content, is_error = normalize_mcp_result(result)
#             return ToolResult(
#                 ok=not is_error,
#                 content=content,
#                 error=content if is_error else None,
#                 metadata={"source": "mcp", "server": self.server_name},
#             )
#         except Exception as e:
#             logger.exception("MCP 工具执行失败: %s", name)
#             return ToolResult(ok=False, error=str(e),
#                               metadata={"source": "mcp", "server": self.server_name})

#     async def close(self) -> None:
#         try:
#             await self.session.__aexit__(None, None, None)
#         except Exception:
#             logger.exception("关闭 MCP session 失败: %s", self.server_name)

class MCPToolProvider(ToolProvider):
    """
    用 FastMCP Client 包装的 MCP 工具提供者。

    用法:
        provider = MCPToolProvider(Client("fs_server.py"), server_name="fs")
        registry.add_provider(provider)

    需要 env / cwd / 命令参数时:
        from fastmcp.client.transports import StdioTransport
        transport = StdioTransport(
            command="python", args=["fs_server.py"], env={"API_KEY": "..."}
        )
        provider = MCPToolProvider(Client(transport), server_name="fs")
    """

    def __init__(self, client: Any, server_name: str, prefix: bool = True):
        self._client = client
        self._stack = AsyncExitStack()
        self._entered = False

        self.server_name = server_name
        self.prefix = prefix

        # 对外名 → ToolSpec
        self._specs: dict[str, ToolSpec] = {}
        # 对外名 → MCP 原始工具名
        self._raw_name: dict[str, str] = {}

    @property
    def name(self) -> str:
        return f"mcp:{self.server_name}"

    def _public_name(self, raw: str) -> str:
        return f"{self.server_name}_{raw}" if self.prefix else raw

    async def _ensure_entered(self) -> None:
        """懒加载：首次使用时才真正连接 server。"""
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
                input_schema=t.inputSchema
                or {"type": "object", "properties": {}},
                metadata={
                    "source": "mcp",
                    "server": self.server_name,
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
                ok=False,
                error=str(e),
                metadata={"source": "mcp", "server": self.server_name},
            )
        # 注意：FastMCP 的 call_tool 在工具错误时抛 ToolError，
        # 所以能走到这里说明调用成功。isError 保留作为防御性检查。
        is_error = bool(getattr(result, "isError", False))

        # FastMCP 的 content 已是 ContentBlock 列表，直接序列化
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


# def normalize_mcp_result(result) -> tuple[Any, bool]:
#     """
#     MCP 返回 { content: [...], isError: bool }
#     把 content 数组转成统一结构。
#     """
#     is_error = bool(getattr(result, "isError", False))
#     raw_content = getattr(result, "content", result)
#     content = []
#     if isinstance(raw_content, list):
#         for item in raw_content:
#             if hasattr(item, "model_dump"):
#                 content.append(item.model_dump())
#             elif hasattr(item, "text"):
#                 content.append({"type": "text", "text": item.text})
#             else:
#                 content.append(item)
#     else:
#         content = raw_content
#     return content, is_error

# ============================================================
# 6. ToolRegistry：聚合与路由
# ============================================================

class ToolRegistry:
    def __init__(self):
        self._providers: list[ToolProvider] = []
        # 对外名 → spec，refresh() 后填充
        self._specs: dict[str, ToolSpec] = {}
        # 对外名 → provider，调用时路由用。
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
        return list({'name': spec.name, 'description': spec.description, 'input_schema': spec.input_schema} for spec in self._specs.values())

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



async def agent_loop(messages):
    while True:
        response = client.messages.create(model=model,
                                        system = SYSTEM_PROMPT, 
                                        messages=messages,
                                        tools = tool_registry.to_llm(),
                                        max_tokens=8000) 
        messages.append({"role": "assistant", "content": response.content})
        tool_blocks = [block for block in response.content if block.type == "tool_use"]
        if not tool_blocks:
            return
        results=[]
        for block in tool_blocks:
            result = await tool_registry.invoke(block.name, block.input)
            result = result.content if result.ok else {result.error}
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
        messages.append({"role": "user", "content": results})

model = os.getenv("MODEL")
gd_api_key = os.getenv("AMAP_MAPS_API_KEY")
client = Anthropic(api_key=os.getenv("LLM_API_KEY"), base_url=os.getenv("LLM_BASE_URL"))
tool_registry = ToolRegistry()
tool_registry.add_provider(LocalToolProvider())
transport = StdioTransport(
    command="npx", args=["-y", "@amap/amap-maps-mcp-server"],
    env={"AMAP_MAPS_API_KEY": gd_api_key},
)
tool_registry.add_provider(MCPToolProvider(Client(transport), server_name="gd"))





async def main():
    try:
        await tool_registry.refresh()
        messages = []
        while True:
            try:
                user_input = input("User: ")
            except EOFError:
                break
            if user_input.strip().lower() in {"exit", "quit"}:
                break
            messages.append({"role": "user", "content": user_input})
            await agent_loop(messages)
            logger.debug(f"Messages: {messages[-3:] if len(messages) >= 3 else messages}")
            logger.debug(f"last Message: {messages[-1]}")
            # print("Assistant:", messages[-1]["content"])
            for block in messages[-1]["content"]:
                if getattr(block,'type',None) == "text":
                    print(f"Assistant: {block.text}")

    finally:
        await tool_registry.close()


if __name__ == "__main__":
    setup_logger()
    asyncio.run(main())
