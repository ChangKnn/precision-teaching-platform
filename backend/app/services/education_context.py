"""Optional university context; legacy school payloads remain byte-for-byte shaped alike."""
from __future__ import annotations

import re

from ..config import settings


def is_higher_education(teaching: dict) -> bool:
    if teaching.get('education_stage'):
        return teaching['education_stage'] == '高等教育'
    grade = str(teaching.get('grade') or '')
    return grade.startswith(('大一','大二','大三','大四','大五','本科','大学','硕士','博士','研究生','研一','研二','研三','博一','博二','博三','博四'))


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


def education_source_note(teaching: dict, note: str) -> str:
    """Remove legacy missing-subject warnings only when subject is inapplicable."""
    if not is_higher_education(teaching):
        return note
    sentences = re.split(r"(?<=[。！？])", note)
    missing_subject = re.compile(r"(?:学科|subject)[^。！？]{0,24}(?:为空|缺失|未提供|未填写|未设置)", re.I)
    kept = [sentence for sentence in sentences if not missing_subject.search(sentence)]
    return "".join(kept).strip() or "本分析依据本次课程教学信息与班级诊断生成，请结合课程大纲及课程目标核对。"
