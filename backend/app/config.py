from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = "思阶 AI 精准教学工作台"
    app_env: str = os.getenv("APP_ENV", "development")
    database_path: Path = Path(os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "precision_teaching.db")))
    seed_demo_data: bool = os.getenv("SEED_DEMO_DATA", "0") == "1"
    frontend_path: Path = PROJECT_ROOT / "frontend"
    upload_path: Path = Path(os.getenv("UPLOAD_PATH", str(PROJECT_ROOT / "data" / "uploads")))
    skills_path: Path = PROJECT_ROOT / "backend" / "skills"
    ai_provider: str = os.getenv("AI_PROVIDER", "mock")
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    ai_api_key: str = os.getenv("AI_API_KEY", "")
    ai_model: str = os.getenv("AI_MODEL", "")
    student_ai_provider: str = os.getenv("STUDENT_AI_PROVIDER", "mock")
    student_ai_base_url: str = os.getenv("STUDENT_AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    student_ai_api_key: str = os.getenv("STUDENT_AI_API_KEY", "")
    student_ai_model: str = os.getenv("STUDENT_AI_MODEL", "")


settings = Settings()
