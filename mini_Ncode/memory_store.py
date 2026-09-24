"""记忆文件的读写、索引、路径检查和记录校验。"""
from __future__ import annotations

import re
from pathlib import Path
import yaml
from .config import MEMORY_DIR, MEMORY_INDEX, MEMORY_TYPES, TEMPORARY_MEMORY_MARKERS, WORKDIR


def parse_memory_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(metadata, dict):
        return {}, text
    return metadata, parts[2].lstrip()


def memory_slug(name: str) -> str:
    slug = re.sub(r"[^\w]+", "-", name.lower()).strip("-_")
    return slug or "memory"


def memory_path(filename: str, allow_index: bool = False) -> Path:
    if Path(filename).name != filename:
        raise ValueError(f"Invalid memory filename: {filename}")
    if filename == MEMORY_INDEX.name and not allow_index:
        raise ValueError("The memory index is not a memory record")

    root = MEMORY_DIR.resolve()
    if not root.is_relative_to(WORKDIR.resolve()):
        raise ValueError("Memory directory escapes the workspace")
    path = (root / filename).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Memory path escapes the store: {filename}")
    return path


def _normalized_memory_text(value: str) -> str:
    return " ".join(value.lower().split())


def should_store_memory(candidate: dict, existing: list[dict]) -> bool:
    if not isinstance(candidate, dict):
        return False
    if candidate.get("scope") != "persistent":
        return False
    if candidate.get("type") not in MEMORY_TYPES:
        return False

    name = str(candidate.get("name", "")).strip()
    description = str(candidate.get("description", "")).strip()
    body = str(candidate.get("body", "")).strip()
    if not name or not description or not body:
        return False

    candidate_text = _normalized_memory_text(f"{name}\n{description}\n{body}")
    if any(marker in candidate_text for marker in TEMPORARY_MEMORY_MARKERS):
        return False

    slug = memory_slug(name)
    norm_desc = _normalized_memory_text(description)
    norm_body = _normalized_memory_text(body)
    for memory in existing:
        if memory_slug(str(memory.get("name", ""))) == slug:
            return False
        if _normalized_memory_text(str(memory.get("description", ""))) == norm_desc:
            return False
        if _normalized_memory_text(str(memory.get("body", ""))) == norm_body:
            return False
    return True


def memory_document(name: str, mem_type: str, description: str, body: str) -> str:
    metadata = yaml.safe_dump(
        {"name": name, "description": description, "type": mem_type},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{metadata}\n---\n\n{body.strip()}\n"


def write_memory_file(name: str, mem_type: str, description: str, body: str) -> Path:
    if not name.strip():
        raise ValueError("Memory name cannot be empty")
    if mem_type not in MEMORY_TYPES:
        raise ValueError(f"Unknown memory type: {mem_type}")
    if not description.strip() or not body.strip():
        raise ValueError("Memory description and body cannot be empty")

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = memory_path(f"{memory_slug(name)}.md")
    path.write_text(
        memory_document(name, mem_type, description, body), encoding="utf-8"
    )
    rebuild_memory_index()
    return path


def rebuild_memory_index() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    for path in sorted(MEMORY_DIR.glob("*.md")):
        if path.name == MEMORY_INDEX.name:
            continue
        try:
            path = memory_path(path.name)
        except ValueError:
            continue
        metadata, body = parse_memory_frontmatter(path.read_text(encoding="utf-8"))
        name = " ".join(str(metadata.get("name") or path.stem).split())
        first_line = next((line for line in body.splitlines() if line.strip()), "")
        description = " ".join(
            str(metadata.get("description") or first_line).split()
        )
        lines.append(f"- [{name}]({path.name}) - {description}")
    memory_path(MEMORY_INDEX.name, allow_index=True).write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


def read_memory_index() -> str:
    try:
        path = memory_path(MEMORY_INDEX.name, allow_index=True)
    except ValueError:
        return ""
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def read_memory_file(filename: str) -> str | None:
    try:
        path = memory_path(filename)
    except ValueError:
        return None
    return path.read_text(encoding="utf-8") if path.is_file() else None


def list_memory_files() -> list[dict]:
    records: list[dict] = []
    if not MEMORY_DIR.exists():
        return records
    for path in sorted(MEMORY_DIR.glob("*.md")):
        if path.name == MEMORY_INDEX.name:
            continue
        try:
            path = memory_path(path.name)
        except ValueError:
            continue
        metadata, body = parse_memory_frontmatter(path.read_text(encoding="utf-8"))
        records.append({
            "filename": path.name,
            "name": str(metadata.get("name") or path.stem),
            "description": str(metadata.get("description") or ""),
            "type": str(metadata.get("type") or "project"),
            "body": body.strip(),
        })
    return records


def validate_memory_record(record, require_scope: bool = False) -> dict | None:
    if not isinstance(record, dict):
        return None
    name = str(record.get("name", "")).strip()
    mem_type = str(record.get("type", "")).strip()
    description = str(record.get("description", "")).strip()
    body = str(record.get("body", "")).strip()
    scope = str(record.get("scope", "")).strip()
    if not name or mem_type not in MEMORY_TYPES or not description or not body:
        return None
    if require_scope and scope not in ("persistent", "current_task"):
        return None

    validated = {
        "name": name, "type": mem_type,
        "description": description, "body": body,
    }
    if scope:
        validated["scope"] = scope
    return validated
