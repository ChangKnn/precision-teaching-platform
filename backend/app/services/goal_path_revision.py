"""Apply a model's small replacements to a copy of the teacher's existing plan."""
from __future__ import annotations

import copy
import re
from typing import Any


REVISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["changes"],
    "properties": {
        "changes": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["path", "value"],
                "properties": {
                    "path": {"type": "string", "minLength": 2},
                    "value": {"type": ["string", "number", "boolean", "object", "array", "null"]},
                },
            },
        },
    },
}

REVISION_INSTRUCTIONS = """
本次是修改已有方案，输出格式改为changes，不输出整份方案。
只修改teacher_revision_request要求涉及的内容，保留其余教师编辑。
每项包含path（从根开始的JSON Pointer路径，如/intervention_path/stages/0/stage_name）
和value（直接填写新值：文字用字符串，数字用数字，列表用数组，对象用对象，空值用null）。
不把新值再次编码为JSON文字，不使用value_json。所有路径必须指向原稿已有字段。
例如：{"changes":[{"path":"/intervention_path/stages/0/stage_name","value":"案例导入"}]}。
优先替换最小的文字字段；仅改表述、名称、简介或按新版规则优化时，保持原学生名单、
目标归属、目标层级、阶段顺序、组织形式和时长，不重复输出这些列表。
教师明确要求调整上述结构时才修改相关字段；增删或重排列表时替换该列表，保持目标引用、
学生归属和活动安排一致。不改学生原始诊断、身份、schema版本或教师确认状态。
目标编号和学生ID保留原值，不依据示例补造学生。未要求调整的内容不改，返回空changes也可以。
"""


def apply_goal_path_revision(previous: dict[str, Any], revision: dict[str, Any]) -> dict[str, Any]:
    """Replace existing, non-overlapping paths; never mutate the saved original."""
    result = copy.deepcopy(previous)
    allowed_roots = {"common_core_goal", "progression_goals", "student_goal_assignments",
                     "intervention_path", "teacher_confirmation"}
    decoded = []
    for change in revision["changes"]:
        path = change["path"]
        if not path.startswith("/") or re.search(r"~(?![01])", path):
            raise ValueError("修改结果包含无效字段路径，原方案已保留")
        parts = tuple(part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/"))
        if parts[0] not in allowed_roots or parts == ("teacher_confirmation", "status"):
            raise ValueError("修改结果涉及不允许修改的系统字段，原方案已保留")
        for old_parts, _ in decoded:
            if parts[:len(old_parts)] == old_parts or old_parts[:len(parts)] == parts:
                raise ValueError("修改结果包含重复或冲突字段，原方案已保留")
        decoded.append((parts, copy.deepcopy(change["value"])))
    for parts, value in decoded:
        current = result
        try:
            for index, part in enumerate(parts):
                if isinstance(current, list):
                    if not re.fullmatch(r"0|[1-9][0-9]*", part):
                        raise KeyError(part)
                    key = int(part)
                elif isinstance(current, dict) and part in current:
                    key = part
                else:
                    raise KeyError(part)
                old_value = current[key]
                if index == len(parts) - 1:
                    current[key] = value
                else:
                    current = old_value
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("修改结果引用了不存在的字段，原方案已保留") from error
    return result
