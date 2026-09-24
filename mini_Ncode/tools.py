"""创建并注册本地工具，每个应用拥有独立的 Todo 和技能依赖。"""
from __future__ import annotations

import glob as glob_module
import subprocess
from .config import WORKDIR
from .providers import LocalToolProvider
from .skills import Skillloader
from .subagent import run_subagent
from .todo import TodoManager


def create_local_provider(*, client, model: str, skills: Skillloader, todo: TodoManager) -> LocalToolProvider:
    local_provider = LocalToolProvider()

    @local_provider.tool(
        name="bash",
        description="Run a shell command.",
        schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        dangerous=True,
    )
    def run_bash(command: str) -> str:
        try:
            r = subprocess.run(
                command, shell=True, cwd=WORKDIR,
                capture_output=True, text=True, errors="replace", timeout=120,encoding="utf-8",
            )
            out = (r.stdout + r.stderr).strip()
            return out[:50000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (120s)"


    @local_provider.tool(
        name="read_file",
        description="Read file contents.",
        schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        },
        read_only=True,
    )
    def run_read(path: str, limit: int | None = None) -> str:
        try:
            lines = (WORKDIR / path).resolve().read_text(encoding="utf-8").splitlines()
            if limit and limit < len(lines):
                lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"


    @local_provider.tool(
        name="write_file",
        description="Write content to a file.",
        schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        dangerous=True,
    )
    def run_write(path: str, content: str) -> str:
        try:
            file_path = (WORKDIR / path).resolve()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error: {e}"


    @local_provider.tool(
        name="edit_file",
        description="Replace exact text in a file once.",
        schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
        dangerous=True,
    )
    def run_edit(path: str, old_text: str, new_text: str) -> str:
        try:
            file_path = (WORKDIR / path).resolve()
            text = file_path.read_text(encoding="utf-8")
            if old_text not in text:
                return f"Error: text not found in {path}"
            file_path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
            return f"Edited {path}"
        except Exception as e:
            return f"Error: {e}"


    @local_provider.tool(
        name="glob",
        description="Find files matching a glob pattern; ** matches recursively.",
        schema={
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
        read_only=True,
    )
    def run_glob(pattern: str) -> str:
        try:
            matches = sorted({
                match for match in glob_module.glob(
                    pattern, root_dir=WORKDIR, recursive=True)
                if (WORKDIR / match).resolve().is_relative_to(WORKDIR)
            })
            shown = matches[:200]
            if len(matches) > 200:
                shown.append("... (more matches omitted; narrow the pattern)")
            return "\n".join(shown) if shown else "(no matches)"
        except Exception as e:
            return f"Error: {e}"


    @local_provider.tool(
        name="todo_write",
        description="Create and manage a task list for your current coding session.",
        schema={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "minLength": 1},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                        "required": ["content", "status"],
                    },
                }
            },
            "required": ["todos"],
        },
    )
    def run_todo_write(todos: list | str) -> str:
        try:
            output = todo.update(todos)
        except ValueError as e:
            return f"Error: {e}"
        print(f"\n\033[33m## Current Tasks\033[0m\n{output}")
        return output


    @local_provider.tool(
        name="load_skill",
        description="Load the full instructions of a named skill.",
        schema={
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1}},
            "required": ["name"],
        },
        read_only=True,
    )
    def run_load_skill(name: str) -> str:
        return skills.load(name)


    @local_provider.tool(
        name="task",
        description=(
            "Run a subagent with fresh conversation context and return its final text."
        ),
        schema={
            "type": "object",
            "properties": {"prompt": {"type": "string", "minLength": 1}},
            "required": ["prompt"],
        },
    )
    def task_tool(prompt: str) -> str:
        return run_subagent(prompt, client=client, model=model, local_provider=local_provider)

    return local_provider
