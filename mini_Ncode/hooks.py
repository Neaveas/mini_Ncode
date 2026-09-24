"""钩子调度、权限检查和工具调用日志。"""
from __future__ import annotations

import re
from typing import Any, Callable
from .config import WORKDIR


class HookManager:
    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable]] = {
            "UserPromptSubmit": [], "PreToolUse": [],
            "PostToolUse": [], "Stop": [],
        }

    def register(self, event: str, callback: Callable) -> None:
        self._hooks[event].append(callback)

    def trigger(self, event: str, *args) -> Any:
        for callback in self._hooks[event]:
            result = callback(*args)
            if result is not None:
                return result
        return None


DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if="]
DESTRUCTIVE_COMMAND_WORD = re.compile(
    r"(?i)(?:^|[;&|()\n])\s*(?:rm|del)(?=\s|$|[;&|()])"
)
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]


def contains_destructive_command(command: str) -> bool:
    return bool(DESTRUCTIVE_COMMAND_WORD.search(command))


def _ask_permission(label: str, block) -> bool:
    print(f"\n\033[33m[permission] {label}\033[0m")
    print(f"   Tool: {block.name}({block.input})")
    return input("   Allow? [y/N] ").strip().lower() in ("y", "yes")


def permission_hook(block):
    if block.name == "bash":
        command = block.input.get("command", "")
        for pattern in DENY_LIST:
            if pattern in command:
                print(f"\n\033[31m[blocked] '{pattern}'\033[0m")
                return "Permission denied by deny list"
        if contains_destructive_command(command) or any(
            kw in command for kw in DESTRUCTIVE
        ):
            if not _ask_permission("Potentially destructive command", block):
                return "Permission denied by user"

    if block.name in ("read_file", "write_file", "edit_file"):
        path = block.input.get("path", "")
        if not (WORKDIR / path).resolve().is_relative_to(WORKDIR):
            if not _ask_permission("Access outside workspace", block):
                return "Permission denied by user"
    return None


def log_hook(block):
    args_preview = str(list(block.input.values()))[:80]
    print(f"\033[90m[HOOK] {block.name}({args_preview})\033[0m")
    return None


def large_output_hook(block, output):
    if len(str(output)) > 100000:
        print(
            f"\033[33m[HOOK] Large output from {block.name}: "
            f"{len(str(output))} chars\033[0m"
        )
    return None


def context_inject_hook(query: str):
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    return None


def summary_hook(messages: list):
    tool_count = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tool_count += 1
                elif getattr(b, "type", None) == "tool_result":
                    tool_count += 1
    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")
    return None


def create_hooks() -> HookManager:
    hooks = HookManager()
    hooks.register("UserPromptSubmit", context_inject_hook)
    hooks.register("PreToolUse", permission_hook)
    hooks.register("PreToolUse", log_hook)
    hooks.register("PostToolUse", large_output_hook)
    hooks.register("Stop", summary_hook)
    return hooks
