"""独立上下文的子任务循环；复用主应用的本地工具。"""
from __future__ import annotations

from .config import WORKDIR
from .messages import extract_text


SUBAGENT_SYSTEM_PROMPT = (
    f"你是一个位于 {WORKDIR} 的编程智能体。"
    "请完成给定的任务，然后返回简洁的最终答案。"
)

SUBAGENT_TOOL_NAMES = ("bash", "read_file", "write_file", "edit_file", "glob")
SUBAGENT_MAX_TURNS = 30


def run_subagent(prompt: str, *, client, model: str, local_provider) -> str:
    """Subagent：独立消息列表、独立循环，只返回最终文本。"""
    print("\n\033[35m[Subagent started]\033[0m")

    sub_specs = [
        local_provider._tools[name][0]
        for name in SUBAGENT_TOOL_NAMES
        if name in local_provider._tools
    ]
    sub_tools = [
        {"name": s.name, "description": s.description, "input_schema": s.input_schema}
        for s in sub_specs
    ]

    messages = [{"role": "user", "content": prompt}]

    for _ in range(SUBAGENT_MAX_TURNS):
        response = client.messages.create(
            model=model,
            system=SUBAGENT_SYSTEM_PROMPT,
            messages=messages,
            tools=sub_tools,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if not tool_calls:
            print("\033[35m[Subagent done]\033[0m")
            return extract_text(response.content) or "(no summary)"

        results = []
        for block in tool_calls:
            entry = local_provider._tools.get(block.name)
            if entry is None:
                output = f"Unknown: {block.name}"
            else:
                _, handler = entry
                try:
                    output = str(handler(**block.input))
                except Exception as e:
                    output = f"Error: {e}"
            print(f"  \033[90m[sub] {block.name}: {output[:100]}\033[0m")
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
        messages.append({"role": "user", "content": results})

    print("\033[35m[Subagent stopped]\033[0m")
    return "Subagent stopped after 30 turns without a final answer."
