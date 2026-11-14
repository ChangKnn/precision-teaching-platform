from __future__ import annotations

from dataclasses import dataclass, field
import copy
import hashlib
import json
import re
from threading import RLock
from time import monotonic
from uuid import uuid4

from fastapi import BackgroundTasks

from ..config import settings
from ..database import database, fetch_all, fetch_one, utc_now, write_audit
from .ai import run_class_diagnosis_report
from .skills import load_skill
from .education_context import higher_education_fields


LEVELS = ("P", "U", "M", "R", "EA")
LEVEL_NAMES = {
    "P": "前结构", "U": "单点结构", "M": "多点结构",
    "R": "关联结构", "EA": "拓展抽象结构",
}


@dataclass
class _ReportTask:
    token: str
    source_hash: str
    stamp: str
    queued_at: float = field(default_factory=monotonic)


# BackgroundTasks run in the single application worker. A live owner, not elapsed
# model time, determines whether a report is still being generated.
_task_lock = RLock()
_report_tasks: dict[tuple[str, str, str], _ReportTask] = {}
_running_tasks: dict[tuple[str, str, str], str] = {}
_INTERRUPTED = "班级报告生成因服务重启或任务中断而停止，请点击重新生成。原报告已保留。"


def _task_key(teaching_id: str, classroom_id: str) -> tuple[str, str, str]:
    return str(settings.database_path.resolve()), teaching_id, classroom_id


def recover_interrupted_class_reports() -> int:
    """Fail orphaned work at startup without discarding previously saved reports.

    Deployment uses one Uvicorn worker; another worker must not share this queue.
    Repeated calls within this process leave its queued/running tasks untouched.
    """
    with _task_lock:
        rows = fetch_all(
            "SELECT teaching_id, classroom_id, source_hash FROM class_diagnosis_reports WHERE status IN ('preparing', 'queued', 'running')"
        )
        orphaned = [row for row in rows if _task_key(row["teaching_id"], row["classroom_id"]) not in _report_tasks]
        active_tokens = {task.token for task in _report_tasks.values()} | set(_running_tasks.values())
        interrupted_jobs = [
            job["id"] for job in fetch_all(
                "SELECT id FROM ai_jobs WHERE skill_key = ? AND status = 'running'", ("solo_class_diagnosis_intervention",)
            ) if job["id"] not in active_tokens
        ]
        if not orphaned and not interrupted_jobs:
            return 0
        now = utc_now()
        with database() as connection:
            for row in orphaned:
                connection.execute(
                    "UPDATE class_diagnosis_reports SET status = 'failed', error = ?, updated_at = ? WHERE teaching_id = ? AND classroom_id = ? AND status IN ('preparing', 'queued', 'running')",
                    (_INTERRUPTED, now, row["teaching_id"], row["classroom_id"]),
                )
            for job_id in interrupted_jobs:
                connection.execute("UPDATE ai_jobs SET status = 'failed', error = ?, completed_at = ? WHERE id = ? AND status = 'running'", (_INTERRUPTED, now, job_id))
        return len(orphaned)


def class_report_input(teaching_id: str, classroom_id: str) -> tuple[dict | None, str | None, dict[str, str]]:
    teaching = fetch_one(
        "SELECT subject, grade, textbook, title, goal, content, education_stage, course_name, major FROM precision_teachings WHERE id = ?",
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
    submitted_ids = {row['student_id'] for row in fetch_all('''SELECT sts.student_id FROM student_task_sessions sts
        JOIN students s ON s.id=sts.student_id WHERE sts.teaching_id=? AND s.classroom_id=? AND sts.status='submitted' ''', (teaching_id,classroom_id))}
    if {row['student_id'] for row in rows} != submitted_ids:
        return None, None, {}
    results = [json.loads(row["generated_json"]) for row in rows]
    names = {row["student_id"]: row["student_name"] for row in rows}
    contents = [line.strip() for line in teaching["content"].splitlines() if line.strip()]
    payload = {
        "schema_version": "1.0",
        "precision_teaching_context": {
            **higher_education_fields(teaching),
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
    for issue in result.get('common_issues', []):
        if not set(issue['basis_student_ids']) <= source_ids:
            raise ValueError('班级共性问题引用了未知学生')
    for item in result["level_analyses"]:
        members = {student["student_id"] for student in item["students"]}
        expected_members = {
            student["student_id"] for student in next(
                row["students"] for row in expected["distribution"] if row["level"] == item["level"]
            )
        }
        if members != expected_members or item["count"] != len(members):
            raise ValueError(f"{item['level']} 层级学生名单或人数与上游报告不符")
        for difficulty in item.get('difficulty_groups', []):
            if not set(difficulty['student_ids']) <= members:
                raise ValueError('困难分析引用了不属于该层级的学生')
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
    for alert in result.get('individual_attention', []):
        student = source_by_id.get(alert['student_id'])
        sources = (student or {}).get('student_statements', []) + [(student or {}).get('submitted_text', '')]
        if not student or not any(alert['evidence_quote'] in text for text in sources):
            raise ValueError('个体关注依据未匹配该学生原始表达')
    trace = result["traceability"]
    if set(trace["output_student_ids"]) != source_ids or trace["input_student_count"] != len(source_ids):
        raise ValueError("班级报告的追溯学生名单不完整")
    assignments = {item["student_id"]: item["original_level"] for item in trace["level_assignments"]}
    if assignments != {item["student_id"]: item["original_level"] for item in expected["level_assignments"]}:
        raise ValueError("班级报告重新判定了学生层级")
    grouping = result["grouping_recommendations"]
    if not grouping["homogeneous_groups"]:
        raise ValueError("同质分组必须覆盖所有参与诊断的学生")
    for key in ("homogeneous_groups", "heterogeneous_groups"):
        groups = grouping[key]
        if any(not group["students"] for group in groups):
            raise ValueError(f"{key} 中有空小组")
        members = [student["student_id"] for group in groups for student in group["students"]]
        if len(members) != len(set(members)) or not set(members) <= source_ids:
            raise ValueError(f"{key} 中有重复或未知学生")
        if groups and set(members) != source_ids:
            raise ValueError(f"{key} 未覆盖所有参与诊断的学生")
        for group in groups:
            group_ids = {student['student_id'] for student in group['students']}
            support_ids = {sid for entry in group.get('support_breakdown', []) for sid in entry['student_ids']}
            unmapped_ids = set(group.get('support_unmapped_student_ids', []))
            if not (support_ids | unmapped_ids) <= group_ids or support_ids & unmapped_ids:
                raise ValueError('组内教学支持引用了不属于本组的学生')
    if grouping["heterogeneous_groups"] and grouping["heterogeneous_not_recommended_reason"]:
        raise ValueError("已有异质分组时不应同时写不建议的理由")
    if not grouping["heterogeneous_groups"] and not grouping["heterogeneous_not_recommended_reason"]:
        raise ValueError("不建议异质分组时必须说明原因")
    result["traceability"]["level_assignments"] = expected["level_assignments"]
    result["traceability"]["input_student_count"] = expected["total_students"]


def singleton_grouping_note(grouping: dict) -> str:
    if any(len(group.get("students", [])) == 1
           for scheme in ("homogeneous_groups", "heterogeneous_groups")
           for group in grouping.get(scheme, [])):
        return "分组中含单人支持安排，请结合学生需要决定保留、并组或调整；合作活动需另行安排同伴。"
    return ""


def with_student_case_summaries(result: dict, payload: dict | None) -> dict:
    """Build reader-facing cases from the individual diagnoses used by this report."""
    view = copy.deepcopy(result)
    students = {item['student']['student_id']: item for item in (payload or {}).get('student_diagnosis_results', [])}
    def concise(text):
        text = str(text or '').strip()
        # Keep a complete first sentence, rather than cutting off an explanation mid-sentence.
        return re.split(r'(?<=[。！？!?])', text, maxsplit=1)[0].strip()
    for issue in view.get('common_issues', []):
        if payload is None and 'case_summaries' in issue:
            continue
        issue['case_summaries'] = []
        for student_id in issue.get('basis_student_ids', [])[:2]:
            student = students.get(student_id)
            if student:
                issue['case_summaries'].append({
                    'student_name': student['student']['student_name'],
                    'level_reason': concise(student.get('diagnosis', {}).get('rationale')),
                    'obstacle': concise(student.get('progression', {}).get('main_obstacle')),
                })
    for level in view.get('level_analyses', []):
        if payload is None and 'student_cases' in level:
            continue
        cases, used = [], set()
        for evidence in level.get('typical_evidence', []):
            student_id = evidence.get('student_id')
            if student_id in used:
                continue
            student = students.get(student_id, {})
            diagnosis = student.get('diagnosis', {})
            if diagnosis and diagnosis.get('level') != level.get('level'):
                diagnosis, student = {}, {}
            cases.append({'student_name': evidence.get('student_name', ''),
                          'level_reason': concise(diagnosis.get('rationale') or evidence.get('interpretation')),
                          'obstacle': concise(student.get('progression', {}).get('main_obstacle'))})
            used.add(student_id)
            if len(cases) == 2:
                break
        level['student_cases'] = cases
    return view


def render_class_markdown(result: dict, payload: dict | None = None) -> str:
    from .readable_evidence import readable_narrative
    result = readable_narrative(with_student_case_summaries(result, payload))
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
        groups = item.get('difficulty_groups', []) if result.get('problem_grouping_version') == '1.1' else []
        primary = groups[0] if groups else {}
        main_problem = primary.get('problem_description') or (item.get('main_obstacles') or ['暂无'])[0]
        main_suggestion = primary.get('teaching_suggestion') or item['teaching_suggestion']
        evidence = "；".join(
            f"{entry['student_name']}：{entry['level_reason']}" + (f" 主要困难：{entry['obstacle']}" if entry['obstacle'] else "")
            for entry in item["student_cases"]
        )
        lines += [
            f"### {item['level']}（{item['level_name']}）：{item['level_meaning']}", "",
            f"- 人数：{item['count']}",
            f"- 学生：{names(item['students'])}",
            f"- 主要表现：{item['main_performance']}",
            f"- 主要困难：{main_problem}",
        ]
        design = item.get('teaching_design')
        if design:
            lines.extend([f"- 教学建议（内容）：{design['content']}", f"- 教学建议（任务）：{design['task']}", f"- 教学建议（支架）：{design['scaffold']}"])
        else:
            lines.append(f"- 教学建议：{main_suggestion}")
        lines.append(f"- 典型依据：{evidence}")
        if len(groups) > 1:
            lines.append(f"- 其他困难：{groups[1]['problem_description']}")
            lines.append(f"- 针对性建议：{groups[1]['teaching_suggestion']}")
        lines.append("")
    lines += ["## 4. 教学重点与难点", ""]
    for title, item in (("教学重点", priorities["teaching_focus"]), ("教学难点", priorities["teaching_difficulty"])):
        lines += [
            f"### {title}", "", f"- 内容：{item['statement']}",
            f"- 诊断依据：{'；'.join(item['diagnosis_basis'])}",
            f"- 教学目标依据：{'；'.join(item['goal_basis'])}", "",
        ]
    lines += ["## 5. 分组建议", "", f"{grouping['usage_note']}", "", "### 同质分组", ""]
    note = singleton_grouping_note(grouping)
    if note:
        lines += [note, ""]
    if grouping["homogeneous_groups"]:
        for group in grouping["homogeneous_groups"]:
            direction = '按组内学生需要分别支持' if group.get('support_breakdown') else group['progression_direction']
            lines += [
                f"- {group['group_name']}（{group['group_id']}；{names(group['students'])}）：{group['common_characteristics']}；"
                f"进阶方向：{direction}；适用情境：{group['use_case']}"
            ]
            by_id = {student['student_id']: student for student in group['students']}
            mapped = set()
            for support in group.get('support_breakdown', []):
                selected = [by_id[sid] for sid in support['student_ids'] if sid in by_id]
                mapped.update(student['student_id'] for student in selected)
                if selected:
                    lines.append(f"  - {names(selected)}：{support['support']}")
            if group.get('common_characteristics') == '本组由不同学习需要的学生组成，请按具体需要提供支持。':
                missing = [student for student in group['students'] if student['student_id'] not in mapped]
                if missing:
                    lines.append(f"  - {names(missing)}：支持安排需结合个体诊断核对。")
    else:
        lines.append("当前不建议同质分组。")
    lines += ["", "### 异质分组", ""]
    if grouping["heterogeneous_groups"]:
        lines += ["| 小组 | 学生 | 组内互补依据 | 合作方向与共同产出 |", "|---|---|---|---|"]
        for group in grouping["heterogeneous_groups"]:
            lines.append(
                f"| {group['group_name']}（{group['group_id']}） | {names(group['students'])} | "
                f"{group['grouping_rationale']} | {group['collaboration_direction']}；{group['shared_product']} |"
            )
    else:
        lines.append(grouping["heterogeneous_not_recommended_reason"] or "当前不建议异质分组。")
    lines += [
        "",
        "## 6. 后续干预设计依据", "",
        "| 面向学生 | 诊断发现 | 需要促进的认知变化 | 后续设计需要满足的条件 |",
        "|---|---|---|---|",
    ]
    for item in result["intervention_design_basis"]:
        lines.append(
            f"| {names(item['target_students'])} | {item['diagnostic_finding']} | "
            f"{item['intended_cognitive_change']} | {'；'.join(item['design_requirements'])} |"
        )
    from .readable_evidence import tidy_punctuation
    return tidy_punctuation("\n".join(lines))


def report_row(teaching_id: str, classroom_id: str) -> dict | None:
    from .readable_evidence import readable_narrative, readable_text
    from .class_teaching_suggestions import latest_suggestions
    row = fetch_one(
        "SELECT * FROM class_diagnosis_reports WHERE teaching_id = ? AND classroom_id = ?",
        (teaching_id, classroom_id),
    )
    if not row:
        return None
    result = json.loads(row['generated_json']) if row['generated_json'] else None
    payload = None
    if result and row['generated_at']:
        job = fetch_one("SELECT input_json FROM ai_jobs WHERE skill_key = ? AND status = 'completed' AND completed_at = ? AND output_json = ? ORDER BY created_at DESC LIMIT 1", (row['skill_key'], row['generated_at'], row['generated_json']))
        if job:
            try:
                payload = json.loads(job['input_json'])
            except (TypeError, ValueError):
                pass
    if result:
        from .compact_outputs import practical_grouping
        result = practical_grouping(result)
    try:
        display_text = render_class_markdown(result, payload) if result else readable_text(row['report_text'] or '')
    except (KeyError, TypeError, ValueError):
        display_text = readable_text(row['report_text'] or '')
    return {
        "teaching_id": teaching_id,
        "classroom_id": classroom_id,
        "skill_key": row["skill_key"],
        "skill_version": row["skill_version"],
        "provider": row["provider"],
        "status": row["status"],
        "source_hash": row["source_hash"],
        "result": readable_narrative(with_student_case_summaries(result, payload)) if result else None,
        "report_text": display_text,
        "error": row["error"],
        "generated_at": row["generated_at"],
        "updated_at": row["updated_at"],
        "teaching_suggestions": latest_suggestions(teaching_id, classroom_id, row["source_hash"]),
    }


def queue_class_report(
    teaching_id: str, classroom_id: str, background_tasks: BackgroundTasks, *, force: bool = False
) -> dict | None:
    with _task_lock:
        return _queue_class_report_locked(teaching_id, classroom_id, background_tasks, force=force)


def _queue_class_report_locked(
    teaching_id: str, classroom_id: str, background_tasks: BackgroundTasks, *, force: bool
) -> dict | None:
    payload, source_hash, _ = class_report_input(teaching_id, classroom_id)
    if not payload:
        return None
    skill = load_skill("solo_class_diagnosis_intervention")
    row = report_row(teaching_id, classroom_id)
    same_source = bool(row and row["source_hash"] == source_hash and row["skill_version"] == skill.version)
    key = _task_key(teaching_id, classroom_id)
    active = _report_tasks.get(key)
    if active and key not in _running_tasks and monotonic() - active.queued_at >= 120:
        # Only a callback which never started can expire. Model calls and newer
        # inputs waiting for an already-running predecessor have no such limit.
        _report_tasks.pop(key, None)
        active = None
    if same_source and active and active.source_hash == source_hash:
        # Repeated polling, double-clicking and force retries share one live task.
        return row
    if row and row["status"] in ("queued", "running") and not active:
        recover_interrupted_class_reports()
        row = report_row(teaching_id, classroom_id)
    if not force and row and row["status"] not in ("queued", "running") and not same_source and row["generated_at"] and row["result"] and row["skill_version"] == skill.version:
        # A teacher may polish published teaching text without changing any student evidence.
        # The old validated report remains the source for intervention design in that case.
        source_job = fetch_one(
            """SELECT input_json FROM ai_jobs
               WHERE skill_key = ? AND status = 'completed' AND completed_at = ?
               ORDER BY created_at DESC LIMIT 1""",
            (skill.key, row["generated_at"]),
        )
        if source_job:
            previous_input = json.loads(source_job["input_json"])
            previous_input.pop('diagnostic_task_text', None)
            for student in previous_input.get('student_diagnosis_results', []):
                student.pop('student_statements', None)
                student.pop('submitted_text', None)
            previous_evidence = {key: value for key, value in previous_input.items() if key != "precision_teaching_context"}
            current_evidence = {key: value for key, value in payload.items() if key != "precision_teaching_context"}
            if previous_evidence == current_evidence:
                now = utc_now()
                with database() as connection:
                    updated = connection.execute(
                        """UPDATE class_diagnosis_reports
                           SET status = 'ready', source_hash = ?, error = NULL, updated_at = ?
                           WHERE teaching_id = ? AND classroom_id = ? AND source_hash = ? AND generated_at = ?""",
                        (source_hash, now, teaching_id, classroom_id, row["source_hash"], row["generated_at"]),
                    )
                    if updated.rowcount:
                        for table in ("goal_path_designs", "goal_path_analysis_drafts"):
                            connection.execute(
                                f"UPDATE {table} SET class_source_hash = ? WHERE teaching_id = ? AND classroom_id = ? AND class_source_hash = ?",
                                (source_hash, teaching_id, classroom_id, row["source_hash"]),
                            )
                if updated.rowcount:
                    return report_row(teaching_id, classroom_id)
    if same_source and not force:
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
    task = _ReportTask(f"job-{uuid4().hex}", source_hash, now)
    _report_tasks[key] = task
    background_tasks.add_task(generate_class_report, teaching_id, classroom_id, source_hash, task.token)
    return report_row(teaching_id, classroom_id)


def generate_class_report(
    teaching_id: str, classroom_id: str, expected_hash: str, generation_token: str | None = None
) -> None:
    key = _task_key(teaching_id, classroom_id)
    with _task_lock:
        task = _report_tasks.get(key)
        if not task or task.source_hash != expected_hash or (generation_token and task.token != generation_token):
            return
        if key in _running_tasks:
            # The current worker will pick up the latest queued input when done.
            return
        try:
            payload, current_hash, names = class_report_input(teaching_id, classroom_id)
            if not payload or current_hash != expected_hash:
                raise ValueError("诊断输入已变化，请重新生成班级报告。原报告已保留。")
            # Supply actual student expressions for attention alerts without changing
            # the existing source hash or automatically invalidating historical reports.
            statements = fetch_all('''SELECT sts.student_id, sts.submitted_text, m.content
                FROM student_task_sessions sts JOIN students s ON s.id=sts.student_id
                LEFT JOIN student_messages m ON m.session_id=sts.id AND m.role='student'
                WHERE sts.teaching_id=? AND s.classroom_id=? AND sts.status='submitted'
                ORDER BY m.id''', (teaching_id, classroom_id))
            by_student = {}
            for statement in statements:
                item = by_student.setdefault(statement['student_id'], {'student_statements': [], 'submitted_text': statement['submitted_text'] or ''})
                if statement['content']:
                    item['student_statements'].append(statement['content'])
            for student in payload.get('student_diagnosis_results', []):
                student.update(by_student.get(student['student']['student_id'], {'student_statements': [], 'submitted_text': ''}))
            if payload.get('student_diagnosis_results'):
                from ..task_materials import canonical_task_text
                payload['diagnostic_task_text'] = canonical_task_text(teaching_id)
            skill = load_skill("solo_class_diagnosis_intervention")
            job_id = task.token
            with database() as connection:
                claimed = connection.execute(
                    "UPDATE class_diagnosis_reports SET status = 'running' WHERE teaching_id = ? AND classroom_id = ? AND source_hash = ? AND updated_at = ? AND status = 'queued'",
                    (teaching_id, classroom_id, expected_hash, task.stamp),
                )
                if not claimed.rowcount:
                    _report_tasks.pop(key, None)
                    return
                connection.execute(
                    """INSERT INTO ai_jobs (id, skill_key, skill_version, provider, status, input_json, created_at)
                       VALUES (?, ?, ?, ?, 'running', ?, ?)""",
                    (job_id, skill.key, skill.version, settings.ai_provider, json.dumps(payload, ensure_ascii=False), utc_now()),
                )
            _running_tasks[key] = job_id
        except Exception as error:
            try:
                with database() as connection:
                    connection.execute(
                        "UPDATE class_diagnosis_reports SET status = 'failed', error = ?, updated_at = ? WHERE teaching_id = ? AND classroom_id = ? AND source_hash = ? AND updated_at = ? AND status IN ('preparing', 'queued', 'running')",
                        (str(error)[:1000], utc_now(), teaching_id, classroom_id, expected_hash, task.stamp),
                    )
            finally:
                _report_tasks.pop(key, None)
            return
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
        report_text = render_class_markdown(with_student_case_summaries(result, payload))
        output_json = json.dumps(result, ensure_ascii=False)
        completed_at = utc_now()
        latest = class_report_input(teaching_id, classroom_id)[1]
        with _task_lock, database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'completed', output_json = ?, completed_at = ? WHERE id = ?",
                (output_json, completed_at, job_id),
            )
            if latest == expected_hash and _report_tasks.get(key) is task:
                connection.execute(
                    """
                    UPDATE class_diagnosis_reports
                    SET status = 'ready', generated_json = ?, report_text = ?,
                        error = NULL, generated_at = ?, updated_at = ?
                    WHERE teaching_id = ? AND classroom_id = ? AND source_hash = ? AND updated_at = ? AND status = 'running'
                    """,
                    (output_json, report_text, completed_at, completed_at, teaching_id, classroom_id, expected_hash, task.stamp),
                )
            elif _report_tasks.get(key) is task:
                connection.execute(
                    "UPDATE class_diagnosis_reports SET status = 'failed', error = ?, updated_at = ? WHERE teaching_id = ? AND classroom_id = ? AND source_hash = ? AND updated_at = ? AND status = 'running'",
                    ("诊断输入已变化，请重新生成班级报告。原报告已保留。", completed_at, teaching_id, classroom_id, expected_hash, task.stamp),
                )
        write_audit("generate", "class_diagnosis_report", f"{teaching_id}:{classroom_id}", {"job_id": job_id})
    except Exception as error:
        completed_at = utc_now()
        with _task_lock, database() as connection:
            connection.execute(
                "UPDATE ai_jobs SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
                (str(error), completed_at, job_id),
            )
            if _report_tasks.get(key) is task:
                connection.execute(
                    """UPDATE class_diagnosis_reports SET status = 'failed', error = ?, updated_at = ?
                       WHERE teaching_id = ? AND classroom_id = ? AND source_hash = ? AND updated_at = ? AND status = 'running'""",
                    (str(error)[:1000], completed_at, teaching_id, classroom_id, expected_hash, task.stamp),
                )
    finally:
        with _task_lock:
            _running_tasks.pop(key, None)
            pending = _report_tasks.get(key)
            if pending is task:
                _report_tasks.pop(key, None)
                pending = None
        if pending:
            generate_class_report(teaching_id, classroom_id, pending.source_hash, pending.token)
