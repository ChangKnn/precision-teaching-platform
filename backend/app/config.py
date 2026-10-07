from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def project_path(value: str) -> Path:
    """Resolve relative storage paths against the project, not the launch directory."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


_teacher_provider = os.getenv("AI_PROVIDER", "mock")
_student_provider = os.getenv("STUDENT_AI_PROVIDER", "mock")
_openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "")


@dataclass(frozen=True)
class Settings:
    app_name: str = "思阶 AI 精准教学工作台"
    app_env: str = os.getenv("APP_ENV", "development")
    database_path: Path = project_path(os.getenv("DATABASE_PATH", "data/precision_teaching.db"))
    seed_demo_data: bool = os.getenv("SEED_DEMO_DATA", "0") == "1"
    frontend_path: Path = PROJECT_ROOT / "frontend"
    upload_path: Path = project_path(os.getenv("UPLOAD_PATH", "data/uploads"))
    skills_path: Path = PROJECT_ROOT / "backend" / "skills"
    ai_provider: str = _teacher_provider
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    ai_api_key: str = _openrouter_api_key if _teacher_provider == "openrouter" else os.getenv("AI_API_KEY", "")
    ai_model: str = os.getenv("AI_MODEL", "")
    student_ai_provider: str = _student_provider
    student_ai_base_url: str = os.getenv("STUDENT_AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    student_ai_api_key: str = _openrouter_api_key if _student_provider == "openrouter" else os.getenv("STUDENT_AI_API_KEY", "")
    student_ai_model: str = os.getenv("STUDENT_AI_MODEL", "")


settings = Settings()
