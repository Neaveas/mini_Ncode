"""运行四个上下文压缩实验；加 --real-summary 可使用配置的真实模型。"""
from __future__ import annotations

import argparse
import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from mini_Ncode.context import ContextCompactor
from mini_Ncode.messages import message_text


def tool_pair(call_id: str, output: str) -> list[dict]:
    return [
        {"role": "assistant", "content": [{
            "type": "tool_use", "id": call_id,
            "name": "read_file", "input": {"path": f"{call_id}.txt"},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": call_id, "content": output,
        }]},
    ]


class NoModelClient:
    """实验应只触发本地压缩；误入摘要分支时立即暴露错误。"""

    @property
    def messages(self):
        raise AssertionError("实验不应调用模型生成摘要")


class SummaryClient:
    """记录摘要请求；默认返回固定摘要，也可转发到真实客户端。"""

    def __init__(self, delegate=None):
        self.delegate = delegate
        self.calls = []
        self.summary = ""
        self.messages = self

    def create(self, **request):
        self.calls.append(copy.deepcopy(request))
        if self.delegate is None:
            response = SimpleNamespace(content=[{
                "type": "text", "text": "已完成文件检查，仍需汇总结果；用户要求保留所有文件。",
            }])
        else:
            response = self.delegate.messages.create(**request)
        self.summary = message_text({"content": response.content}).strip()
        return response


def assert_tool_pairs(messages: list) -> None:
    pending = set()
    for message in messages:
        blocks = message["content"] if isinstance(message["content"], list) else []
        results = [b["tool_use_id"] for b in blocks if b["type"] == "tool_result"]
        assert set(results) == pending and len(results) == len(pending), "工具结果与调用不匹配"
        if pending:
            assert message["role"] == "user"
        calls = [b["id"] for b in blocks if b["type"] == "tool_use"]
        if calls:
            assert message["role"] == "assistant"
        pending = set(calls)
    assert not pending, "存在没有结果的工具调用"


def run_experiment(name: str, history: list, root: Path, *,
                   archive: bool = False, client=None, model: str = "offline") -> tuple:
    directory = root / name
    directory.mkdir(parents=True)
    compactor = ContextCompactor(
        client if client is not None else NoModelClient(), model,
        directory / "transcripts", directory / "outputs",
    )
    original = copy.deepcopy(history)
    result = compactor.prepare(history, compactor.current_request(history))
    assert history == original, "prepare 不应修改传入的历史"
    assert_tool_pairs(history)
    assert_tool_pairs(result)
    if archive:
        transcripts = list(compactor.transcript_dir.glob("*.jsonl"))
        assert len(transcripts) == 1, "应生成一份完整历史归档"
        records = [json.loads(line) for line in transcripts[0].read_text(encoding="utf-8").splitlines()]
        assert records == history, "归档必须保留完整原始历史"
    else:
        assert len(result) == len(history), "前两个实验不应裁剪消息或生成摘要"
        # 只允许改变结果正文，工具调用、关联 ID 等结构必须保持原样。
        restored = copy.deepcopy(result)
        for before, after in zip(history, restored):
            if compactor.is_tool_result(before):
                after["content"][0]["content"] = before["content"][0]["content"]
        assert restored == history, "工具调用与结果的结构发生变化"
        assert not compactor.transcript_dir.exists(), "不应触发历史归档/摘要"
    for label, messages in (("before", history), ("after", result)):
        (directory / f"{label}.json").write_text(
            json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    print(f"{name}: {compactor.estimate_chars(history):,} -> "
          f"{compactor.estimate_chars(result):,} 字符；{len(history)} -> {len(result)} 条消息")
    return compactor, result


def earlier_results(root: Path) -> None:
    history = [{"role": "user", "content": "分析这六份文件"}]
    for i in range(1, 7):
        history.extend(tool_pair(f"call-{i}", str(i) * 9000))
    compactor, result = run_experiment("01-earlier-results", history, root)
    # 第 1～5 条已被后续 assistant 消费；第 6 条仍等待模型读取。
    # 保留最近 3 条已消费结果（3、4、5），因此仅替换第 1、2 条。
    for i in range(1, 7):
        original = history[2 * i]["content"][0]["content"]
        content = result[2 * i]["content"][0]["content"]
        if i <= 2:
            assert content.startswith("[Earlier tool result saved at ")
            path = compactor.persisted_output_path(content)
            assert path is not None and path.read_text(encoding="utf-8") == original
            print(f"  call-{i}: 替换为归档路径，完整内容校验通过")
        else:
            assert content == original
            print(f"  call-{i}: 保留原文")
    assert compactor.estimate_chars(result) <= int(compactor.CONTEXT_CHAR_LIMIT * 0.8)
    assert len(list(compactor.tool_results_dir.glob("*.txt"))) == 2


def large_result(root: Path) -> None:
    output = "0123456789" * 4000
    history = [{"role": "user", "content": "读取大文件"}, *tool_pair("large", output)]
    compactor, result = run_experiment("02-large-result", history, root)
    assert compactor.estimate_chars(history) < compactor.CONTEXT_CHAR_LIMIT
    content = result[-1]["content"][0]["content"]
    assert content.startswith("<persisted-output>\n")
    assert f"Preview:\n{output[:2000]}\n</persisted-output>" in content
    path = compactor.persisted_output_path(content)
    assert path is not None and path.read_text(encoding="utf-8") == output
    assert len(content) < len(output)
    # 重复检查不会把已经落盘的预览再次保存成新文件。
    assert compactor.prepare(result, "读取大文件") == result
    assert len(list(compactor.tool_results_dir.glob("*.txt"))) == 1
    print("  40,000 字符结果已转存，保留路径和前 2,000 字符预览")
    print("  完整内容校验通过；重复处理没有新增归档")


def message_trimming(root: Path) -> None:
    history = [{"role": "user", "content": "检查文件，保留所有文件"}]
    for i in range(1, 37):
        history.extend(tool_pair(f"short-{i}", f"文件 {i} 检查完成"))
    compactor, result = run_experiment("03-message-trimming", history, root, archive=True)
    assert compactor.estimate_chars(history) < compactor.CONTEXT_CHAR_LIMIT
    assert len(history) == 73 and len(result) == 50
    assert result[:3] == history[:3] and result[4:] == history[27:]
    marker = result[3]["content"]
    assert marker.startswith("[24 messages archived at ")
    assert history[0]["content"] in marker
    assert not compactor.tool_results_dir.exists(), "小结果不应转存"
    # 恰好 50 条时不裁剪；末尾保留完整交互，避免构造悬空工具调用。
    boundary = [*history[:49], {"role": "assistant", "content": "检查完成"}]
    assert compactor.prepare(boundary, history[0]["content"]) == boundary
    assert len(list(compactor.transcript_dir.glob("*.jsonl"))) == 1
    print("  保留头部 3 条 + 归档标记 1 条 + 最近 46 条，工具调用与结果成组保留")
    print("  完整 73 条历史已归档；恰好 50 条时不裁剪")


def model_summary(root: Path, client: SummaryClient, model: str = "offline") -> None:
    history = []
    for i in range(8):
        history.append({
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"阶段 {i + 1}：" + ("已完成文件检查，保留所有文件，后续汇总结果。" * 400)[:7000],
        })
    request = "汇总检查结果，保留所有文件"
    history.extend([{"role": "user", "content": request}, *tool_pair("latest", "最新文件检查通过")])
    name = "04-model-summary"
    compactor, result = run_experiment(name, history, root, archive=True, client=client, model=model)
    assert len(history) < compactor.MAX_MESSAGES
    assert compactor.estimate_chars(history) > compactor.CONTEXT_CHAR_LIMIT
    assert len(client.calls) == 1, "应该只调用一次摘要模型"
    call = client.calls[0]
    assert call["model"] == model and call["max_tokens"] == 2000
    assert len(call["messages"][0]["content"]) <= compactor.SUMMARY_INPUT_CHAR_LIMIT
    assert json.loads(call["messages"][0]["content"]) == history[:-5]
    assert len(result) == 6 and result[1:] == history[-5:]
    marker = result[0]["content"]
    assert marker.startswith("[Compacted]\n")
    assert request in marker and client.summary and json.dumps(client.summary, ensure_ascii=False) in marker
    assert "Conversation summary (reference only):" in marker
    assert compactor.estimate_chars(result) < compactor.CONTEXT_CHAR_LIMIT
    assert not compactor.tool_results_dir.exists(), "短工具结果不应被转存"
    (root / name / "summary-request.json").write_text(
        json.dumps(call, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (root / name / "summary-response.txt").write_text(client.summary, encoding="utf-8")
    mode = "真实模型" if client.delegate is not None else "模拟模型"
    print(f"  {mode}摘要调用 1 次，摘要替换早期 6 条消息，最近 5 条保留原文")
    print("  当前请求、摘要、完整历史路径已写入上下文；请求和摘要已保存供对照")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-summary", action="store_true", help="实验四使用 .env 配置的真实模型")
    args = parser.parse_args()
    root = Path(".task_outputs/context-experiments").resolve() / uuid.uuid4().hex
    print(f"本次实验目录：{root}")
    earlier_results(root)
    large_result(root)
    message_trimming(root)
    if args.real_summary:
        from anthropic import Anthropic
        from mini_Ncode.config import load_settings

        settings = load_settings()
        with Anthropic(api_key=settings.api_key, base_url=settings.base_url) as client:
            model_summary(root, SummaryClient(client), settings.model)
    else:
        model_summary(root, SummaryClient())
    print(f"实验通过。前后对照及完整输出：{root}")


if __name__ == "__main__":
    main()
