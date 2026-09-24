"""应用装配和命令行交互；外部客户端只在 main 中创建。"""
from __future__ import annotations

import asyncio
import logging

from .config import MEMORY_DIR, SKILL_DIR, TRANSCRIPT_DIR, TOOL_RESULTS_DIR, load_settings
from .context import ContextCompactor
from .hooks import create_hooks
from .logging_setup import setup_logger
from .memory_store import rebuild_memory_index
from .providers import MCPToolProvider
from .registry import ToolRegistry
from .runner import agent_loop
from .skills import Skillloader
from .todo import TodoManager
from .tools import create_local_provider

logger = logging.getLogger(__name__)


def setup_readline() -> None:
    try:
        import readline
    except ImportError:
        return
    for binding in (
        "set bind-tty-special-chars off", "set input-meta on",
        "set output-meta on", "set convert-meta off",
    ):
        readline.parse_and_bind(binding)


async def main() -> None:
    from anthropic import Anthropic

    settings = load_settings()
    skills = Skillloader(SKILL_DIR)
    todo = TodoManager()
    hooks = create_hooks()
    registry = ToolRegistry()

    with Anthropic(api_key=settings.api_key, base_url=settings.base_url) as client:
        compactor = ContextCompactor(client, settings.model, TRANSCRIPT_DIR, TOOL_RESULTS_DIR)
        registry.add_provider(create_local_provider(
            client=client, model=settings.model, skills=skills, todo=todo,
        ))

        if settings.amap_api_key:
            from fastmcp import Client
            from fastmcp.client.transports import StdioTransport

            transport = StdioTransport(
                command="npx",
                args=["-y", "@amap/amap-maps-mcp-server"],
                env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
            )
            registry.add_provider(MCPToolProvider(Client(transport), server_name="gd"))

        try:
            MEMORY_DIR.mkdir(exist_ok=True)
            rebuild_memory_index()
            await registry.refresh()
            print("Enter a question, press Enter to send. Type q/exit to quit.\n")

            messages: list = []
            while True:
                try:
                    query = input("\001\033[36m\002You >> \001\033[0m\002")
                except (EOFError, KeyboardInterrupt):
                    break
                if query.strip().lower() in {"exit", "quit", "q", ""}:
                    break

                hooks.trigger("UserPromptSubmit", query)
                messages.append({"role": "user", "content": query})
                await agent_loop(
                    messages, client=client, model=settings.model,
                    tool_registry=registry, skills=skills, hooks=hooks,
                    compactor=compactor, active_request=query,
                )
                logger.debug("Messages: %s", messages[-3:])
                last = messages[-1]["content"]
                for block in last if isinstance(last, list) else []:
                    if getattr(block, "type", None) == "text":
                        print(f"Assistant: {block.text}")
                print()
        finally:
            await registry.close()


def run() -> None:
    setup_logger()
    setup_readline()
    asyncio.run(main())
