from __future__ import annotations

import copy
import hashlib
import json
import re
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from .database import database, fetch_one, utc_now, write_audit
from .goal_path_api import _aliases, _replace_names, _serialized_row, _student_levels
from .services.ai import run_activity_formative_design
from .services.skills import load_skill


router = APIRouter(prefix="/api")

ORGANIZATIONS = {"全班集体干预", "同质小组干预", "异质小组干预", "个体干预"}
ALLOWED_CIRCLES = {
    "全班集体干预": {"师生会话圈", "师生机会话圈"},
    "同质小组干预": {"生生会话圈", "师生机会话圈"},
    "异质小组干预": {"生生会话圈", "师生机会话圈"},
    "个体干预": {"师生会话圈", "生机会话圈", "学生-内容会话圈"},
}


class ActivityRequirements(BaseModel):
    fixed_learning_materials: list[str] = Field(default_factory=list)
    required_activities: list[str] = Field(default_factory=list)
    prohibited_approaches: list[str] = Field(default_factory=list)
    ai_access: str = Field(default="unspecified", pattern="^(none|shared|per_student|mixed|unspecified)$")
    ai_use_constraints: list[str] = Field(default_factory=list)
    teacher_notes: list[str] = Field(default_factory=list)


class ActivityGenerateRequest(BaseModel):
    teacher_activity_requirements: ActivityRequirements = Field(default_factory=ActivityRequirements)


class ActivitySaveRequest(BaseModel):
    result: dict
    confirmed: bool = False
    presentation_overrides: dict[str, dict[str, str]] = Field(default_factory=dict)


def _source(teaching_id: str, classroom_id: str) -> tuple[dict, dict, dict, dict, str]:
    design = _serialized_row(teaching_id, classroom_id)
    if not design or design["status"] != "teacher_confirmed":
        raise HTTPException(status_code=409, detail="请先确认当前班级的目标与活动路径")
    goal_row = fetch_one(
        "SELECT * FROM goal_path_designs WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    class_row = fetch_one(
        "SELECT * FROM class_diagnosis_reports WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    teaching = fetch_one("SELECT * FROM precision_teachings WHERE id = ?", (teaching_id,))
    if not goal_row or not class_row or not teaching or class_row["status"] != "ready":
        raise HTTPException(status_code=409, detail="上游诊断或目标路径已不可用")
    class_result = json.loads(class_row["generated_json"])
    _student_levels(class_result)
    source_hash = hashlib.sha256(
        (goal_row["generated_json"] + goal_row["teacher_context_json"] + class_row["source_hash"]).encode("utf-8")
    ).hexdigest()
    return design["result"], class_result, json.loads(goal_row["teacher_context_json"]), teaching, source_hash


def _build_input(teaching_id: str, classroom_id: str, requirements: dict) -> tuple[dict, dict, str]:
    goal, class_result, teacher_context, teaching, source_hash = _source(teaching_id, classroom_id)
    names = _aliases(class_result)
    levels = _student_levels(class_result)
    by_level = {level: {"count": 0, "student_ids": []} for level in ("P", "U", "M", "R", "EA")}
    for student_id, level in levels.items():
        by_level[level]["student_ids"].append(student_id)
        by_level[level]["count"] += 1
    student_names = {
        student["student_id"]: names[student["student_name"]]
        for item in class_result["class_summary"]["distribution"] for student in item["students"]
    }
    diagnosis_text = json.dumps(_replace_names({
        "overall_diagnosis": class_result["overall_diagnosis"],
        "teaching_priorities": class_result["teaching_priorities"],
        "intervention_design_basis": class_result["intervention_design_basis"],
    }, names), ensure_ascii=False)
    path = goal["intervention_path"]
    payload = {
        "schema_version": "1.0",
        "precision_teaching_context": {
            "subject": teaching["subject"], "grade": teaching["grade"],
            "textbook_version_and_chapter": teaching["textbook"] or "未填写教材章节",
            "precision_teaching_topic": teaching["title"],
            "precision_teaching_goals": [teaching["goal"]],
            "precision_teaching_content": [line.strip() for line in teaching["content"].splitlines() if line.strip()] or [teaching["title"]],
        },
        "diagnosis_bundle": {
            "class_summary": {"total_students": len(levels), "distribution": by_level},
            "traceability": {
                "levels_unchanged": True,
                "level_assignments": [
                    {"student_id": student_id, "student_name": student_names[student_id], "level": level}
                    for student_id, level in levels.items()
                ],
            },
            "class_diagnosis": diagnosis_text,
        },
        "teacher_instructional_context": teacher_context,
        "goal_path_design": {
            "schema_version": goal["schema_version"], "design_status": "teacher_confirmed",
            "common_core_goal": {
                "goal_id": "CG", "goal_content": goal["common_core_goal"]["goal_statement"],
                "observable_attainment": "；".join(goal["common_core_goal"]["observable_success_criteria"]),
            },
            "progression_goals": [
                {
                    "goal_id": item["goal_id"], "goal_level": item["target_level_name"],
                    "target_student_ids": [student["student_id"] for student in item["target_students"]],
                    "goal_content": item["goal_statement"], "observable_attainment": item["observable_achievement"],
                }
                for item in goal["progression_goals"]
            ],
            "student_goal_assignments": [
                {"student_id": item["student_id"], "goal_id": item["primary_goal_id"]}
                for item in goal["student_goal_assignments"]
            ],
            "intervention_path": {
                "primary": {
                    "path_type": path["primary_path_label"],
                    "total_duration_minutes": path["total_duration_minutes"],
                    "stages": [
                        {
                            "stage_id": stage["stage_id"], "stage_name": stage["stage_name"],
                            "duration_minutes": stage["duration_minutes"],
                            "activity_units": [
                                {
                                    "unit_id": unit["unit_id"], "organization_code": unit["organization_code"],
                                    "organization_name": unit["organization_name"], "activity_name": unit["activity_name"],
                                    "target_goal_ids": unit["target_goal_ids"],
                                    "target_student_ids": [student["student_id"] for student in unit["target_students"]],
                                    "activity_summary": unit["activity_summary"],
                                }
                                for unit in stage["activity_units"]
                            ],
                        }
                        for stage in path["stages"]
                    ],
                },
            },
            "teacher_confirmation": {"status": "teacher_confirmed"},
        },
        "teacher_activity_requirements": requirements,
    }
    return payload, goal, source_hash


def _normalize_parallel_activities(result: dict, goal: dict) -> list[str]:
    """Repair only an unambiguous one-unit-per-activity split of a parallel stage."""
    by_id = {item["activity_id"]: item for item in result["activities"]}
    remapped_ids: dict[str, str] = {}
    repaired_stages: list[str] = []
    for stage, summary in zip(goal["intervention_path"]["stages"], result["activity_sequence_summary"]["stages"]):
        units = stage["activity_units"]
        if len(units) < 2 or stage["stage_id"] != summary["stage_id"]:
            continue
        activities = [by_id.get(activity_id) for activity_id in summary["activity_ids"]]
        if any(item is None for item in activities):
            continue
        if len(activities) == 1:
            # The source path, not the model's boolean, determines simultaneity.
            summary["simultaneous"] = True
            continue
        unit_ids = [unit["unit_id"] for unit in units]
        if (len(activities) != len(units) or
                any(item["source_stage_id"] != stage["stage_id"] or len(item["source_unit_ids"]) != 1 for item in activities) or
                set(item["source_unit_ids"][0] for item in activities) != set(unit_ids)):
            continue
        unit_map = {unit["unit_id"]: unit for unit in units}
        merged = copy.deepcopy(activities[0])
        merged_id = f"ACT-{stage['stage_id']}-1"
        merged.update(
            activity_id=merged_id, source_unit_ids=unit_ids, sequence_within_stage=1,
            activity_name=stage["stage_name"], duration_minutes=stage["duration_minutes"],
        )
        for field in ("target_goal_ids", "target_student_ids", "organization_forms", "dialogue_circles", "learning_materials"):
            merged[field] = list(dict.fromkeys(value for item in activities for value in item[field]))
        for field in ("activity_objective", "student_task", "cognitive_processing", "learning_product"):
            merged[field] = "；".join(f"{item['activity_name']}：{item[field]}" for item in activities)
        merged["scaffold_support"] = [support for item in activities for support in item["scaffold_support"]]
        merged["differentiation_note"] = "；".join(filter(None, (item["differentiation_note"] for item in activities))) or None
        group_tasks = []
        for item in activities:
            source_id = item["source_unit_ids"][0]
            source = unit_map[source_id]
            if item["parallel_group_tasks"]:
                if len(item["parallel_group_tasks"]) != 1 or item["parallel_group_tasks"][0]["source_unit_id"] != source_id:
                    group_tasks = []
                    break
                group_tasks.append(item["parallel_group_tasks"][0])
                continue
            form = item["organization_forms"][0]
            group_tasks.append({
                "source_unit_id": source_id, "group_name": item["activity_name"],
                "organization_form": form,
                "dialogue_circles": [circle for circle in item["dialogue_circles"] if circle in ALLOWED_CIRCLES[form]],
                "target_goal_ids": source["target_goal_ids"],
                "target_student_ids": [student["student_id"] for student in source["target_students"]],
                "activity_objective": item["activity_objective"],
                "learning_materials": item["learning_materials"],
                "student_task": item["student_task"],
                "cognitive_processing": item["cognitive_processing"],
                "scaffold_support": item["scaffold_support"],
                "learning_product": item["learning_product"],
            })
        if len(group_tasks) != len(units):
            continue
        merged["parallel_group_tasks"] = group_tasks
        actions = []
        for actor in ("教师", "学生", "同伴", "AI"):
            statements = [f"{item['activity_name']}：{action['action']}" for item in activities for action in item["participant_actions"] if action["actor"] == actor]
            if statements:
                actions.append({"order": len(actions) + 1, "actor": actor, "action": "；".join(statements)})
        if len(actions) < 2:
            continue
        merged["participant_actions"] = actions
        for item in activities:
            remapped_ids[item["activity_id"]] = merged_id
        by_id[merged_id] = merged
        summary["activity_ids"] = [merged_id]
        summary["simultaneous"] = True
        repaired_stages.append(stage["stage_id"])
    if repaired_stages:
        result["activities"] = [by_id[activity_id] for stage in result["activity_sequence_summary"]["stages"] for activity_id in stage["activity_ids"]]
        for evaluation in result["formative_evaluation_nodes"]:
            evaluation["after_activity_ids"] = list(dict.fromkeys(remapped_ids.get(activity_id, activity_id) for activity_id in evaluation["after_activity_ids"]))
        result["teacher_confirmation"]["items_to_confirm"].append(
            "平台已将同阶段并行小组整理为一项活动，请核对各组任务与课堂时间。"
        )
    return repaired_stages


def _normalize_common_goal_mapping(result: dict, goal: dict) -> list[str]:
    """Keep CG in teaching/evaluation text, not in a unit's immutable mapping."""
    unit_map = {
        unit["unit_id"]: unit
        for stage in goal["intervention_path"]["stages"]
        for unit in stage["activity_units"]
    }
    repaired = []
    for activity in result["activities"]:
        source_ids = activity["source_unit_ids"]
        if not set(source_ids) <= set(unit_map):
            continue
        expected = {goal_id for source_id in source_ids for goal_id in unit_map[source_id]["target_goal_ids"]}
        if set(activity["target_goal_ids"]) == expected | {"CG"} and "CG" not in expected:
            activity["target_goal_ids"] = [goal_id for goal_id in activity["target_goal_ids"] if goal_id != "CG"]
            repaired.append(activity["activity_id"])
        for group in activity["parallel_group_tasks"]:
            source = unit_map.get(group["source_unit_id"])
            if not source:
                continue
            group_expected = set(source["target_goal_ids"])
            if set(group["target_goal_ids"]) == group_expected | {"CG"} and "CG" not in group_expected:
                group["target_goal_ids"] = [goal_id for goal_id in group["target_goal_ids"] if goal_id != "CG"]
    if repaired:
        result["teacher_confirmation"]["items_to_confirm"].append(
            "平台已将共同核心目标保留在活动文字与评价中，并按上游单元校正活动目标编号映射。"
        )
    return repaired


def _validate(result: dict, goal: dict) -> None:
    import jsonschema

    skill = load_skill("precision_intervention_activity_formative_regulation")
    schema = json.loads((skill.path.parent / "assets/activity-formative-output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(result, schema)
    if result["design_status"] != result["teacher_confirmation"]["status"]:
        raise ValueError("活动设计状态与教师确认状态不一致")
    path = goal["intervention_path"]
    stages = path["stages"]
    summaries = result["activity_sequence_summary"]["stages"]
    if result["activity_sequence_summary"]["total_duration_minutes"] != path["total_duration_minutes"]:
        raise ValueError("活动总时长与上游路径不一致")
    if [item["stage_id"] for item in summaries] != [item["stage_id"] for item in stages]:
        raise ValueError("活动阶段顺序与上游路径不一致")
    activities = result["activities"]
    by_id = {activity["activity_id"]: activity for activity in activities}
    if len(by_id) != len(activities):
        raise ValueError("活动编号重复")
    all_summary_ids = [activity_id for item in summaries for activity_id in item["activity_ids"]]
    if len(all_summary_ids) != len(activities) or set(all_summary_ids) != set(by_id):
        raise ValueError("活动序列摘要与活动列表不一致")
    goal_ids = {item["goal_id"] for item in goal["progression_goals"]}
    evaluation_goal_ids = goal_ids | {"CG"}
    all_students = {item["student_id"] for item in goal["student_goal_assignments"]}
    unit_map = {unit["unit_id"]: unit for stage in stages for unit in stage["activity_units"]}
    for stage, summary in zip(stages, summaries, strict=True):
        stage_id = stage["stage_id"]
        if summary["duration_minutes"] != stage["duration_minutes"] or summary["stage_name"] != stage["stage_name"]:
            raise ValueError("活动阶段名称或时长与上游路径不一致")
        stage_units = {unit["unit_id"] for unit in stage["activity_units"]}
        stage_activities = [by_id[activity_id] for activity_id in summary["activity_ids"]]
        if sum(item["duration_minutes"] for item in stage_activities) != stage["duration_minutes"]:
            raise ValueError("阶段内顺序活动时长之和与上游时长不一致")
        if len(stage_units) > 1 and (len(stage_activities) != 1 or not summary["simultaneous"]):
            raise ValueError("同阶段并行单元必须合并为一项活动")
        used_units = []
        for index, activity in enumerate(stage_activities, 1):
            if activity["source_stage_id"] != stage_id or activity["activity_id"] != f"ACT-{stage_id}-{index}" or activity["sequence_within_stage"] != index:
                raise ValueError("活动编号、阶段或顺序不一致")
            used_units += activity["source_unit_ids"]
            if not set(activity["source_unit_ids"]) <= stage_units:
                raise ValueError("活动引用了其他阶段的单元")
            source_units = [unit_map[unit_id] for unit_id in activity["source_unit_ids"]]
            allowed_goals = set().union(*(set(unit["target_goal_ids"]) for unit in source_units))
            allowed_students = set().union(*(set(student["student_id"] for student in unit["target_students"]) for unit in source_units))
            if set(activity["target_goal_ids"]) != allowed_goals or set(activity["target_student_ids"]) != allowed_students:
                raise ValueError("活动的目标或学生范围与上游单元不一致")
            if not set(activity["target_goal_ids"]) <= goal_ids or not set(activity["target_student_ids"]) <= all_students:
                raise ValueError("活动引用了不存在的目标或学生")
            if not set(activity["organization_forms"]) <= ORGANIZATIONS:
                raise ValueError("活动出现非法组织形式")
            circles = set(activity["dialogue_circles"])
            if not circles <= set().union(*(ALLOWED_CIRCLES[form] for form in activity["organization_forms"])):
                raise ValueError("会话圈与组织形式不匹配")
            group_tasks = activity["parallel_group_tasks"]
            if len(source_units) > 1:
                if {group["source_unit_id"] for group in group_tasks} != set(activity["source_unit_ids"]) or len(group_tasks) != len(source_units):
                    raise ValueError("并行小组任务与上游单元未一一对应")
            elif group_tasks:
                raise ValueError("单单元活动不应生成并行小组任务")
            for group in group_tasks:
                source = unit_map[group["source_unit_id"]]
                if set(group["target_goal_ids"]) != set(source["target_goal_ids"]) or set(group["target_student_ids"]) != {student["student_id"] for student in source["target_students"]}:
                    raise ValueError("并行小组的目标或学生范围与上游单元不一致")
                if not set(group["dialogue_circles"]) <= ALLOWED_CIRCLES[group["organization_form"]]:
                    raise ValueError("并行小组会话圈与组织形式不匹配")
            orders = [action["order"] for action in activity["participant_actions"]]
            if orders != list(range(1, len(orders) + 1)):
                raise ValueError("活动过程动作顺序不连续")
        if set(used_units) != stage_units or (len(stage_units) > 1 and len(used_units) != len(stage_units)):
            raise ValueError("上游活动单元存在遗漏或重复")
    evaluations = result["formative_evaluation_nodes"]
    for evaluation in evaluations:
        if not set(evaluation["after_activity_ids"]) <= set(by_id):
            raise ValueError("形成性评价引用了不存在的活动")
        if not set(evaluation["target_goal_ids"]) <= evaluation_goal_ids or not set(evaluation["target_student_ids"]) <= all_students:
            raise ValueError("形成性评价引用了不存在的目标或学生")
        if not {criterion["goal_id"] for criterion in evaluation["judgment_criteria"]} <= set(evaluation["target_goal_ids"]):
            raise ValueError("评价标准引用了范围外的目标")


def _markdown(result: dict, presentation_overrides: dict | None = None) -> str:
    def cell(value: str) -> str:
        return str(value).replace("|", "\\|").replace("\n", "<br>")

    def numbered(value: str) -> str:
        lines = [re.sub(r"^\s*\d+[.、)]\s*", "", line).strip() for line in value.splitlines()]
        return "\n".join(f"{index}. {line}" for index, line in enumerate((line for line in lines if line), 1))

    presentation_overrides = presentation_overrides or {}
    lines = ["# 学习活动与形成性评价设计", "", "## 一、学习活动设计", ""]
    for index, activity in enumerate(result["activities"], 1):
        override = presentation_overrides.get(activity["activity_id"], {})
        actions = {actor: [item["action"] for item in activity["participant_actions"] if item["actor"] == actor] for actor in ("教师", "学生", "同伴", "AI")}
        student_actions = actions["学生"] + [f"同伴：{item}" for item in actions["同伴"]]
        group_tasks = [f"{group['group_name']}：{group['student_task']}；产出：{group['learning_product']}" for group in activity["parallel_group_tasks"]]
        supports = [f"{item['target_students_or_group']}：{item['support']}" for item in activity["scaffold_support"]]
        lines += [
            f"### 活动{index}：{activity['activity_name']}（{activity['duration_minutes']}分钟；{'、'.join(activity['organization_forms'])}）", "",
            "| 项目 | 教师活动 | 学生活动 | AI辅助 |",
            "| --- | --- | --- | --- |",
            f"| 活动目标 | {cell(override.get('objective', activity['activity_objective']))} |  |  |",
            f"| 活动过程 | {cell('；'.join(actions['教师']))} | {cell(numbered(override['student']) if override.get('student') else '；'.join(student_actions + supports + group_tasks))} | {cell('；'.join(actions['AI']))} |",
            f"| 学习材料与资源 | {cell('、'.join(activity['learning_materials']))} |  |  |",
            f"| 学习产出 | {cell(override.get('product', activity['learning_product']))} |  |  |", "",
        ]
    lines += ["## 二、形成性评价", "", "| 评价时机 | 对象与目标 | 评价任务与证据 | 达成标准 | 评价结果处理 | 判断主体 |", "| --- | --- | --- | --- | --- | --- |"]
    for evaluation in result["formative_evaluation_nodes"]:
        activity_number = next((index for index, item in enumerate(result["activities"], 1) if item["activity_id"] == evaluation["after_activity_ids"][-1]), "?")
        rules = evaluation["adjustment_rules"]
        criteria = "；".join(f"{item['goal_id']}：{item['meets_when']}" for item in evaluation["judgment_criteria"])
        adjustment = f"达到：{rules['if_goal_reached']}；尚未达到：{rules['if_not_yet']}；个人证据不清：{rules['if_evidence_not_individual']}"
        lines.append(f"| 活动{activity_number}之后 | {cell(evaluation['evaluation_purpose'])} | {cell(evaluation['evidence_task'] + '；' + evaluation['evidence_description'])} | {cell(criteria)} | {cell(adjustment)} | {cell(evaluation['decision_by'])} |")
    lines += ["", "## 三、请教师确认", ""] + [f"- {item}" for item in result["teacher_confirmation"]["items_to_confirm"]]
    return "\n".join(lines)


def _serialized(teaching_id: str, classroom_id: str) -> dict | None:
    row = fetch_one(
        "SELECT * FROM activity_formative_designs WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    if not row:
        return None
    try:
        _, _, _, _, source_hash = _source(teaching_id, classroom_id)
    except HTTPException:
        source_hash = None
    status = row["status"] if source_hash == row["goal_path_hash"] else "stale"
    return {
        "teaching_id": teaching_id, "classroom_id": classroom_id,
        "skill_key": row["skill_key"], "skill_version": row["skill_version"],
        "provider": row["provider"], "status": status,
        "result": json.loads(row["generated_json"]), "report_text": row["report_text"],
        "presentation_overrides": json.loads(row["presentation_overrides_json"]),
        "generated_at": row["generated_at"], "confirmed_at": row["confirmed_at"], "updated_at": row["updated_at"],
    }


@router.get("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/activity-formative")
def get_activity_formative(teaching_id: str, classroom_id: str) -> dict:
    try:
        goal, _, _, _, _ = _source(teaching_id, classroom_id)
        ready = True
        source_summary = {
            "total_duration_minutes": goal["intervention_path"]["total_duration_minutes"],
            "stage_count": len(goal["intervention_path"]["stages"]),
        }
    except HTTPException:
        ready = False
        source_summary = None
    return {"goal_path_ready": ready, "source_summary": source_summary,
            "design": _serialized(teaching_id, classroom_id)}


@router.post("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/activity-formative/generate")
def generate_activity_formative(teaching_id: str, classroom_id: str, request: ActivityGenerateRequest) -> dict:
    skill = load_skill("precision_intervention_activity_formative_regulation")
    payload, goal, source_hash = _build_input(
        teaching_id, classroom_id, request.teacher_activity_requirements.model_dump()
    )
    try:
        import jsonschema
        input_schema = json.loads((skill.path.parent / "assets/activity-formative-input.schema.json").read_text(encoding="utf-8"))
        jsonschema.validate(payload, input_schema)
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"活动设计输入校验失败：{error}") from error
    job_id = f"job-{uuid4().hex}"
    with database() as connection:
        connection.execute(
            "INSERT INTO ai_jobs (id, skill_key, skill_version, provider, status, input_json, created_at) VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (job_id, skill.key, skill.version, settings.ai_provider, json.dumps(payload, ensure_ascii=False), utc_now()),
        )
    result = None
    try:
        result = run_activity_formative_design(skill, payload, settings.ai_provider)
        result["design_status"] = "pending_teacher_confirmation"
        result["teacher_confirmation"]["status"] = "pending_teacher_confirmation"
        _normalize_parallel_activities(result, goal)
        _normalize_common_goal_mapping(result, goal)
        _validate(result, goal)
        report_text = _markdown(result)
        now = utc_now()
        output_json = json.dumps(result, ensure_ascii=False)
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'completed', output_json = ?, completed_at = ? WHERE id = ?",
                (output_json, now, job_id),
            )
            connection.execute(
                """
                INSERT INTO activity_formative_designs
                (teaching_id, classroom_id, skill_key, skill_version, provider, status,
                 goal_path_hash, generated_json, report_text, generated_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending_teacher_confirmation', ?, ?, ?, ?, ?)
                ON CONFLICT(teaching_id, classroom_id) DO UPDATE SET
                    skill_key = excluded.skill_key, skill_version = excluded.skill_version,
                    provider = excluded.provider, status = excluded.status, goal_path_hash = excluded.goal_path_hash,
                    generated_json = excluded.generated_json, report_text = excluded.report_text,
                    presentation_overrides_json = '{}',
                    generated_at = excluded.generated_at, confirmed_at = NULL, updated_at = excluded.updated_at
                """,
                (teaching_id, classroom_id, skill.key, skill.version, settings.ai_provider,
                 source_hash, output_json, report_text, now, now),
            )
        write_audit("generate", "activity_formative_design", f"{teaching_id}:{classroom_id}", {"job_id": job_id})
        return {"design": _serialized(teaching_id, classroom_id)}
    except Exception as error:
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'failed', output_json = ?, error = ?, completed_at = ? WHERE id = ?",
                (json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else None, str(error), utc_now(), job_id),
            )
        raise HTTPException(status_code=502, detail=f"学习活动生成失败：{error}") from error


@router.put("/precision-teachings/{teaching_id}/classrooms/{classroom_id}/activity-formative")
def save_activity_formative(teaching_id: str, classroom_id: str, request: ActivitySaveRequest) -> dict:
    existing = _serialized(teaching_id, classroom_id)
    if not existing or existing["status"] == "stale":
        raise HTTPException(status_code=409, detail="活动方案不存在或上游目标路径已更新，请重新生成")
    goal, _, _, _, _ = _source(teaching_id, classroom_id)
    result = copy.deepcopy(request.result)
    result["design_status"] = "teacher_confirmed" if request.confirmed else "pending_teacher_confirmation"
    result["teacher_confirmation"]["status"] = result["design_status"]
    try:
        _validate(result, goal)
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"活动方案校验失败：{error}") from error
    activity_ids = {activity["activity_id"] for activity in result["activities"]}
    overrides = {
        activity_id: {key: value.strip() for key, value in fields.items() if key in {"objective", "student", "product"} and value.strip()}
        for activity_id, fields in request.presentation_overrides.items() if activity_id in activity_ids
    }
    overrides = {activity_id: fields for activity_id, fields in overrides.items() if fields}
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            UPDATE activity_formative_designs SET status = ?, generated_json = ?, report_text = ?, presentation_overrides_json = ?,
                confirmed_at = ?, updated_at = ? WHERE teaching_id = ? AND classroom_id = ?
            """,
            (result["design_status"], json.dumps(result, ensure_ascii=False), _markdown(result, overrides), json.dumps(overrides, ensure_ascii=False),
             now if request.confirmed else None, now, teaching_id, classroom_id),
        )
    write_audit("confirm" if request.confirmed else "save", "activity_formative_design", f"{teaching_id}:{classroom_id}", {})
    return {"design": _serialized(teaching_id, classroom_id)}
