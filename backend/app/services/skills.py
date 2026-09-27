from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..database import database, utc_now


@dataclass(frozen=True)
class SkillDefinition:
    key: str
    name: str
    version: str
    path: Path
    instructions: str


def _metadata(text: str, key: str, fallback: str) -> str:
    match = re.search(rf"^\s*{re.escape(key)}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def load_skill(skill_key: str) -> SkillDefinition:
    skill_path = settings.skills_path / skill_key.replace("_", "-") / "SKILL.md"
    if not skill_path.exists():
        raise KeyError(f"未知 Skill：{skill_key}")
    instructions = skill_path.read_text(encoding="utf-8")
    return SkillDefinition(
        key=skill_key,
        name=_metadata(instructions, "name", skill_key),
        version=_metadata(instructions, "version", "0.1.0"),
        path=skill_path,
        instructions=instructions,
    )


def sync_skills() -> None:
    for skill_dir in settings.skills_path.iterdir():
        if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").exists():
            continue
        skill = load_skill(skill_dir.name.replace("-", "_"))
        with database() as connection:
            connection.execute(
                "UPDATE skill_versions SET active = 0 WHERE skill_key = ? AND version <> ?",
                (skill.key, skill.version),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO skill_versions
                (skill_key, name, version, source_path, active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (skill.key, skill.name, skill.version, str(skill.path), utc_now()),
            )


def list_skills() -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for skill_dir in sorted(settings.skills_path.iterdir()):
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
            skill = load_skill(skill_dir.name.replace("-", "_"))
            result.append({"key": skill.key, "name": skill.name, "version": skill.version})
    return result
