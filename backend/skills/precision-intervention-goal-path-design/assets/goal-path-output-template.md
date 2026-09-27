# 精准干预目标与课堂路径设计

## 1. 共同核心目标

**核心目标：** {{common_core_goal_statement}}

- **目标认知结构及理由：** {{target_solo_level_and_name}}——{{structure_and_rationale}}
- **可观察的达成表现：** {{observable_success_criteria}}

## 2. 分层进阶目标

| 目标层级 | 具体目标内容 | 相关学生姓名 | 可观察的达成表现 |
|---|---|---|---|
| {{target_level_name_and_source_levels}} | {{goal_statement}} | {{student_names}} | {{observable_achievement}} |

## 3. 推荐课堂组织与推进

- **主要组织模式：** {{primary_path_label}}
- **模式说明：** {{mode_explanation}}
- **推荐理由：** {{recommendation_reasons}}
- **课堂推进安排：** {{teacher_facing_path}}
- **备选组织模式：** {{alternative_path_or_none}}

## 4. 课堂活动路径

| 阶段 | 活动名称 | 组织形式 | 活动内容简介 | 面向学生与目标 | 建议时长 |
|---|---|---|---|---|---:|
| {{stage_id}} | {{activity_name}} | {{organization_name_and_code}} | {{activity_summary}} | {{target_students_and_goals}} | {{stage_duration_minutes}}分钟 |

同一阶段出现多行表示这些活动同时开展；其建议时长按该阶段计算一次，不重复相加。

## 5. 请教师确认

- **当前状态：** 待教师确认
- **请确认：** {{items_to_confirm}}
- **尚未确定：** {{unresolved_decisions_or_none}}

## 机器可读结果

在上述Markdown后附加：

```json precision_intervention_goal_path
{
  "schema_version": "1.2",
  "design_status": "pending_teacher_confirmation",
  "common_core_goal": {},
  "progression_goals": [],
  "student_goal_assignments": [],
  "intervention_path": {},
  "teacher_confirmation": {},
  "handoff": {}
}
```

实际输出必须填写全部必需字段并通过 `goal-path-output.schema.json`，不得保留占位符。
