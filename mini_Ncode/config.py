"""路径、记忆参数与启动时读取的环境配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


WORKDIR = Path.cwd()
MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
SKILL_DIR = WORKDIR / ".skills"

MEMORY_TYPES = ("user", "feedback", "project", "reference")
TEMPORARY_MEMORY_MARKERS = (
    "this session", "current session", "this turn", "current turn",
    "this task", "current task", "for now", "just this time", "today only",
    "本次会话", "当前会话", "这一轮", "当前轮次",
    "本次任务", "当前任务", "暂时",
)
RECALL_CHAR_LIMIT = 20000
CONSOLIDATE_THRESHOLD = 10
CONSOLIDATE_INPUT_CHAR_LIMIT = 20000


@dataclass(frozen=True)
class Settings:
    model: str
    api_key: str | None
    base_url: str | None
    amap_api_key: str | None


def load_settings() -> Settings:
    from dotenv import load_dotenv

    load_dotenv(override=True)
    model = os.getenv("MODEL") or os.getenv("MODEL_ID")
    if not model:
        raise RuntimeError("请设置 MODEL 或 MODEL_ID 环境变量")
    return Settings(
        model=model,
        api_key=os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL"),
        amap_api_key=os.getenv("AMAP_MAPS_API_KEY"),
    )
