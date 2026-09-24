"""技能目录扫描和按名称加载。"""
from __future__ import annotations

from pathlib import Path
import yaml


class Skillloader:
    def __init__(self, skill_dir: Path):
        self.skill_dir = skill_dir
        self.skills: dict[str, dict[str, str]] = {}
        self.scan()

    @staticmethod
    def parse_frontmatter(text: str) -> tuple[dict, str]:
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip("\r\n") != "---":
            return {}, text
        close_index = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip("\r\n") == "---"),
            None,
        )
        if close_index is None:
            return {}, text
        frontmatter = "".join(lines[1:close_index])
        body = "".join(lines[close_index + 1:])
        try:
            metadata = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return metadata, body

    def scan(self) -> None:
        self.skills.clear()
        if not self.skill_dir.exists():
            return
        skill_root = self.skill_dir.resolve()
        for manifest in sorted(self.skill_dir.glob("*/SKILL.md")):
            if (
                not manifest.is_file()
                or not manifest.parent.is_dir()
                or not manifest.parent.resolve().is_relative_to(skill_root)
            ):
                continue
            content = manifest.read_text(encoding="utf-8")
            metadata, body = self.parse_frontmatter(content)
            raw_name = metadata.get("name")
            name = raw_name.strip() if isinstance(raw_name, str) else ""
            name = name or manifest.parent.name
            raw_desc = metadata.get("description")
            description = raw_desc if isinstance(raw_desc, str) else ""
            description = description or body.split("\n", 1)[0]
            description = "".join(str(description).lstrip("#").strip())
            self.skills[name] = {
                "name": name, "description": description, "content": body,
            }

    def catalog(self) -> str:
        if not self.skills:
            return "No skills found."
        return "\n".join(
            f"- {s['name']}: {s['description']}" for s in self.skills.values()
        )

    def load(self, skill_name: str) -> str:
        skill = self.skills.get(skill_name)
        if skill:
            return skill["content"]
        return (
            f"Error: Skill '{skill_name}' not found. "
            f"Available skills: {', '.join(self.skills.keys())}"
        )
