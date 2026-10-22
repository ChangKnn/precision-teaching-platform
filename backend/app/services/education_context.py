"""Optional university context; legacy school payloads remain byte-for-byte shaped alike."""
from __future__ import annotations

from ..config import settings


def is_higher_education(teaching: dict) -> bool:
    if teaching.get('education_stage'):
        return teaching['education_stage'] == '高等教育'
    grade = str(teaching.get('grade') or '')
    return grade.startswith(('大一','大二','大三','大四','大五','本科','大学','硕士','博士','研究生'))


def higher_education_fields(teaching: dict) -> dict:
    if not is_higher_education(teaching):
        return {}
    result = {'education_stage': '高等教育'}
    for key in ('course_name','major'):
        value = str(teaching.get(key) or '').strip()
        if value:
            result[key] = value
    return result


def education_runtime_rules() -> str:
    return (settings.skills_path/'shared/education-context-rules.md').read_text(encoding='utf-8')
