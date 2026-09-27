from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from fastapi import BackgroundTasks

from ..config import settings
from ..database import database, fetch_all, fetch_one, utc_now, write_audit
from .ai import run_class_diagnosis_report
from .skills import load_skill


LEVELS = ("P", "U", "M", "R", "EA")
LEVEL_NAMES = {
    "P": "前结构", "U": "单点结构", "M": "多点结构",
    "R": "关联结构", "EA": "拓展抽象结构",
}


def class_report_input(teaching_id: str, classroom_id: str) -> tuple[dict | None, str | None, dict[str, str]]:
    teaching = fetch_one(
        "SELECT subject, grade, textbook, title, goal, content FROM precision_teachings WHERE id = ?",
        (teaching_id,),
    )
    if not teaching:
        return None, None, {}
    rows = fetch_all(
        """
        SELECT s.id AS student_id, s.name AS student_name, r.generated_json
        FROM student_diagnosis_reports r
        JOIN student_task_sessions sts ON sts.id = r.session_id
        JOIN students s ON s.id = sts.student_id
        WHERE r.teaching_id = ? AND s.classroom_id = ?
          AND sts.status = 'submitted' AND r.status != 'stale' AND r.provider != 'mock'
        ORDER BY s.id
        """,
        (teaching_id, classroom_id),
    )
    if not rows:
        return None, None, {}
    results = [json.loads(row["generated_json"]) for row in rows]
    names = {row["student_id"]: row["student_name"] for row in rows}
    contents = [line.strip() for line in teaching["content"].splitlines() if line.strip()]
    payload = {
        "schema_version": "1.0",
        "precision_teaching_context": {
            "subject": teaching["subject"],
            "grade": teaching["grade"],
            "textbook_version_and_chapter": teaching["textbook"] or "未填写教材章节",
            "precision_teaching_topic": teaching["title"],
            "precision_teaching_goals": [teaching["goal"]],
            "precision_teaching_content": contents or [teaching["title"]],
        },
        "student_diagnosis_results": results,
    }
    source_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload, source_hash, names


def deterministic_distribution(payload: dict) -> dict:
    buckets = {level: [] for level in LEVELS}
    assignments = []
    seen = set()
    for result in payload["student_diagnosis_results"]:
        student = result["student"]
        student_id = student["student_id"]
        level = result["diagnosis"]["level"]
        if student_id in seen or level not in LEVELS or result["progression"]["current_level"] != level:
            raise ValueError(f"学生个体诊断编号或层级冲突：{student_id}")
        seen.add(student_id)
        buckets[level].append({"student_id": student_id, "student_name": student["student_name"]})
        assignments.append({"student_id": student_id, "original_level": level})
    total = len(seen)
    return {
        "total_students": total,
        "distribution": [
            {
                "level": level,
                "level_name": LEVEL_NAMES[level],
                "count": len(buckets[level]),
                "proportion": len(buckets[level]) / total,
                "students": buckets[level],
            }
            for level in LEVELS
        ],
        "level_assignments": assignments,
    }


def validate_class_result(result: dict, payload: dict) -> None:
    expected = deterministic_distribution(payload)
    model_rows = result["class_summary"]["distribution"]
    if [item["level"] for item in model_rows] != list(LEVELS):
        raise ValueError("班级报告未按 P/U/M/R/EA 顺序覆盖五个层级")
    for model_row, exact_row in zip(model_rows, expected["distribution"]):
        if not model_row["level_meaning"].strip():
            raise ValueError("班级报告缺少本任务的层级含义")
        if model_row["count"] != exact_row["count"] or {
            item["student_id"] for item in model_row["students"]
        } != {item["student_id"] for item in exact_row["students"]}:
            raise ValueError("班级报告人数或名单与个体报告不一致")
        model_row.update(exact_row)
    result["class_summary"]["total_students"] = expected["total_students"]
    source_ids = {item["student_id"] for item in expected["level_assignments"]}
    nonempty = {row["level"] for row in expected["distribution"] if row["count"]}
    if {item["level"] for item in result["level_analyses"]} != nonempty:
        raise ValueError("班级报告的分层分析与非空层级不一致")
    source_by_id = {item["student"]["student_id"]: item for item in payload["student_diagnosis_results"]}
    for item in result["level_analyses"]:
        members = {student["student_id"] for student in item["students"]}
        expected_members = {
            student["student_id"] for student in next(
                row["students"] for row in expected["distribution"] if row["level"] == item["level"]
            )
        }
        if members != expected_members or item["count"] != len(members):
            raise ValueError(f"{item['level']} 层级学生名单或人数与上游报告不符")
        used_evidence = set()
        verified_evidence = []
        for evidence in item["typical_evidence"]:
            student = source_by_id.get(evidence["student_id"])
            if evidence["student_id"] not in members or not student:
                raise ValueError("班级报告引用了不属于该层级的学生")
            sources = student["diagnosis"]["evidence"]
            source = next(
                (entry for entry in sources if entry["ref"] == evidence["source_ref"]
                 and (evidence["student_id"], entry["ref"]) not in used_evidence),
                None,
            )
            if source is None:
                source = next(
                    (entry for entry in sources if (evidence["student_id"], entry["ref"]) not in used_evidence),
                    None,
                )
            if source is None:
                continue
            used_evidence.add((evidence["student_id"], source["ref"]))
            evidence["student_name"] = student["student"]["student_name"]
            evidence["source_ref"] = source["ref"]
            evidence["quote"] = source["quote"]
            evidence["interpretation"] = source["interpretation"]
            verified_evidence.append(evidence)
        if not verified_evidence:
            raise ValueError("班级报告缺少可追溯的典型证据")
        item["typical_evidence"] = verified_evidence
    trace = result["traceability"]
    if set(trace["output_student_ids"]) != source_ids or trace["input_student_count"] != len(source_ids):
        raise ValueError("班级报告的追溯学生名单不完整")
    assignments = {item["student_id"]: item["original_level"] for item in trace["level_assignments"]}
    if assignments != {item["student_id"]: item["original_level"] for item in expected["level_assignments"]}:
        raise ValueError("班级报告重新判定了学生层级")
    for key in ("homogeneous_groups", "heterogeneous_groups"):
        members = [student["student_id"] for group in result["grouping_recommendations"][key] for student in group["students"]]
        if len(members) != len(set(members)) or not set(members) <= source_ids:
            raise ValueError(f"{key} 中有重复或未知学生")
    result["traceability"]["level_assignments"] = expected["level_assignments"]
    result["traceability"]["input_student_count"] = expected["total_students"]


def render_class_markdown(result: dict) -> str:
    summary = result["class_summary"]
    overall = result["overall_diagnosis"]
    priorities = result["teaching_priorities"]
    grouping = result["grouping_recommendations"]
    names = lambda students: "、".join(student["student_name"] for student in students) or "无"
    lines = [
        "# 班级 SOLO 聚合诊断结果", "",
        "## 1. 班级 SOLO 分布", "",
        "| SOLO 层级及本任务含义 | 人数 | 比例 | 学生 |",
        "|---|---:|---:|---|",
    ]
    for item in summary["distribution"]:
        meaning = item["level_meaning"].replace("|", "｜").replace("\n", " ")
        lines.append(
            f"| {item['level']}（{item['level_name']}）：{meaning} | {item['count']} | "
            f"{item['proportion']:.1%} | {names(item['students'])} |"
        )
    lines += [
        "", f"总人数：{summary['total_students']}", "",
        "## 2. 班级整体诊断", "",
        f"- 主要层级：{'、'.join(f'{level}（{LEVEL_NAMES[level]}）' for level in overall['dominant_levels'])}",
        f"- 整体思维结构特点：{overall['overall_characteristics']}",
        f"- 层级差异：{overall['differentiation_summary']}",
        f"- 主要进阶方向：{overall['main_progression_direction']}", "",
        "## 3. 各层级学生分析", "",
    ]
    for item in result["level_analyses"]:
        evidence = "；".join(
            f"{entry['student_name']} {entry['source_ref']}：“{entry['quote']}”（{entry['interpretation']}）"
            for entry in item["typical_evidence"]
        )
        lines += [
            f"### {item['level']}（{item['level_name']}）：{item['level_meaning']}", "",
            f"- 人数：{item['count']}",
            f"- 学生：{names(item['students'])}",
            f"- 主要表现：{item['main_performance']}",
            f"- 典型依据：{evidence}",
            f"- 主要进阶障碍：{'；'.join(item['main_obstacles'])}", "",
        ]
    lines += ["## 4. 教学重点与难点", ""]
    for title, item in (("教学重点", priorities["teaching_focus"]), ("教学难点", priorities["teaching_difficulty"])):
        lines += [
            f"### {title}", "", f"- 内容：{item['statement']}",
            f"- 诊断依据：{'；'.join(item['diagnosis_basis'])}",
            f"- 教学目标依据：{'；'.join(item['goal_basis'])}", "",
        ]
    lines += ["## 5. 分组建议", "", f"{grouping['usage_note']}", "", "### 同质分组", ""]
    if grouping["homogeneous_groups"]:
        for group in grouping["homogeneous_groups"]:
            lines += [
                f"- {group['group_id']}（{names(group['students'])}）：{group['common_characteristics']}；"
                f"进阶方向：{group['progression_direction']}；适用情境：{group['use_case']}"
            ]
    else:
        lines.append("当前不建议同质分组。")
    lines += ["", "### 异质分组", ""]
    if grouping["heterogeneous_groups"]:
        lines += ["| 小组 | 学生 | 组内互补依据 | 合作方向与共同产出 |", "|---|---|---|---|"]
        for group in grouping["heterogeneous_groups"]:
            lines.append(
                f"| {group['group_id']} | {names(group['students'])} | "
                f"{group['grouping_rationale']} | {group['collaboration_direction']}；{group['shared_product']} |"
            )
    else:
        lines.append(grouping["heterogeneous_not_recommended_reason"] or "当前不建议异质分组。")
    lines += [
        "", f"未分组学生：{names(grouping['ungrouped_students'])}", "",
        "## 6. 后续干预设计依据", "",
        "| 面向学生 | 诊断发现 | 需要促进的认知变化 | 后续设计需要满足的条件 |",
        "|---|---|---|---|",
    ]
    for item in result["intervention_design_basis"]:
        lines.append(
            f"| {names(item['target_students'])} | {item['diagnostic_finding']} | "
            f"{item['intended_cognitive_change']} | {'；'.join(item['design_requirements'])} |"
        )
    return "\n".join(lines)


def report_row(teaching_id: str, classroom_id: str) -> dict | None:
    row = fetch_one(
        "SELECT * FROM class_diagnosis_reports WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    if not row:
        return None
    return {
        "teaching_id": teaching_id,
        "classroom_id": classroom_id,
        "skill_key": row["skill_key"],
        "skill_version": row["skill_version"],
        "provider": row["provider"],
        "status": row["status"],
        "source_hash": row["source_hash"],
        "result": json.loads(row["generated_json"]) if row["generated_json"] else None,
        "report_text": row["report_text"],
        "error": row["error"],
        "generated_at": row["generated_at"],
        "updated_at": row["updated_at"],
    }


def queue_class_report(
    teaching_id: str, classroom_id: str, background_tasks: BackgroundTasks, *, force: bool = False
) -> dict | None:
    payload, source_hash, _ = class_report_input(teaching_id, classroom_id)
    if not payload:
        return None
    skill = load_skill("solo_class_diagnosis_intervention")
    row = report_row(teaching_id, classroom_id)
    same_source = bool(row and row["source_hash"] == source_hash and row["skill_version"] == skill.version)
    if same_source and not force:
        if row["status"] != "queued":
            return row
        # A queued job can be orphaned by a process restart.
        queued_at = datetime.fromisoformat(row["updated_at"])
        if (datetime.now(timezone.utc) - queued_at).total_seconds() < 120:
            return row
    now = utc_now()
    with database() as connection:
        connection.execute(
            """
            INSERT INTO class_diagnosis_reports
            (teaching_id, classroom_id, skill_key, skill_version, provider, status,
             source_hash, updated_at)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
            ON CONFLICT(teaching_id, classroom_id) DO UPDATE SET
                skill_key = excluded.skill_key, skill_version = excluded.skill_version,
                provider = excluded.provider, status = 'queued',
                source_hash = excluded.source_hash, error = NULL, updated_at = excluded.updated_at
            """,
            (teaching_id, classroom_id, skill.key, skill.version, settings.ai_provider, source_hash, now),
        )
    background_tasks.add_task(generate_class_report, teaching_id, classroom_id, source_hash)
    return report_row(teaching_id, classroom_id)


def generate_class_report(teaching_id: str, classroom_id: str, expected_hash: str) -> None:
    payload, current_hash, names = class_report_input(teaching_id, classroom_id)
    if not payload or current_hash != expected_hash:
        return
    skill = load_skill("solo_class_diagnosis_intervention")
    job_id = f"job-{uuid4().hex}"
    with database() as connection:
        connection.execute(
            "UPDATE class_diagnosis_reports SET status = 'running', updated_at = ? WHERE teaching_id = ? AND classroom_id = ? AND source_hash = ?",
            (utc_now(), teaching_id, classroom_id, expected_hash),
        )
        connection.execute(
            """
            INSERT INTO ai_jobs (id, skill_key, skill_version, provider, status, input_json, created_at)
            VALUES (?, ?, ?, ?, 'running', ?, ?)
            """,
            (job_id, skill.key, skill.version, settings.ai_provider, json.dumps(payload, ensure_ascii=False), utc_now()),
        )
    try:
        import jsonschema

        input_schema = json.loads((skill.path.parent / "assets/class-diagnosis-input.schema.json").read_text(encoding="utf-8"))
        jsonschema.validate(payload, input_schema)
        exact = deterministic_distribution(payload)
        result, _ = run_class_diagnosis_report(skill, payload, settings.ai_provider)
        validate_class_result(result, payload)
        def restore_names(value):
            if isinstance(value, dict):
                if "student_id" in value and "student_name" in value:
                    value["student_name"] = names.get(value["student_id"], value["student_name"])
                for nested in value.values():
                    restore_names(nested)
            elif isinstance(value, list):
                for nested in value:
                    restore_names(nested)
        restore_names(result)
        result["class_summary"]["total_students"] = exact["total_students"]
        report_text = render_class_markdown(result)
        output_json = json.dumps(result, ensure_ascii=False)
        completed_at = utc_now()
        latest = class_report_input(teaching_id, classroom_id)[1]
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'completed', output_json = ?, completed_at = ? WHERE id = ?",
                (output_json, completed_at, job_id),
            )
            if latest == expected_hash:
                connection.execute(
                    """
                    UPDATE class_diagnosis_reports
                    SET status = 'ready', generated_json = ?, report_text = ?,
                        error = NULL, generated_at = ?, updated_at = ?
                    WHERE teaching_id = ? AND classroom_id = ? AND source_hash = ?
                    """,
                    (output_json, report_text, completed_at, completed_at, teaching_id, classroom_id, expected_hash),
                )
        write_audit("generate", "class_diagnosis_report", f"{teaching_id}:{classroom_id}", {"job_id": job_id})
    except Exception as error:
        completed_at = utc_now()
        with database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
                (str(error), completed_at, job_id),
            )
            connection.execute(
                """
                UPDATE class_diagnosis_reports SET status = 'failed', error = ?, updated_at = ?
                WHERE teaching_id = ? AND classroom_id = ? AND source_hash = ?
                """,
                (str(error)[:1000], completed_at, teaching_id, classroom_id, expected_hash),
            )
