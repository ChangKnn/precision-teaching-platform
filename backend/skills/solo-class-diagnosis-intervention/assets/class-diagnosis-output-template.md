# 班级 SOLO 聚合诊断结果

## 1. 班级 SOLO 分布

| SOLO 层级及含义 | 人数 | 比例 | 学生 |
|---|---:|---:|---|
| P（前结构）：{{task_specific_meaning_P}} | {{count_P}} | {{percentage_P}} | {{students_P}} |
| U（单点结构）：{{task_specific_meaning_U}} | {{count_U}} | {{percentage_U}} | {{students_U}} |
| M（多点结构）：{{task_specific_meaning_M}} | {{count_M}} | {{percentage_M}} | {{students_M}} |
| R（关联结构）：{{task_specific_meaning_R}} | {{count_R}} | {{percentage_R}} | {{students_R}} |
| EA（拓展抽象结构）：{{task_specific_meaning_EA}} | {{count_EA}} | {{percentage_EA}} | {{students_EA}} |

总人数：{{total_students}}

## 2. 班级整体诊断

- 主要层级：{{dominant_levels_with_names_and_meanings}}
- 整体思维结构特点：{{overall_characteristics}}
- 层级差异：{{differentiation_summary}}
- 主要进阶方向：{{main_progression_direction}}

## 3. 各层级学生分析

仅呈现有学生的层级。

### {{level_code}}（{{level_name}}）：{{task_specific_level_meaning}}

- 人数：{{count}}
- 学生：{{students}}
- 主要表现：{{main_performance}}
- 典型依据：{{typical_evidence_with_student_and_ref}}
- 主要进阶障碍：{{main_obstacles}}
- 教学建议：{{brief_teaching_suggestion}}

## 4. 教学重点与难点

### 教学重点

- 内容：{{teaching_focus}}
- 诊断依据：{{focus_diagnosis_basis}}
- 教学目标依据：{{focus_goal_basis}}

### 教学难点

- 内容：{{teaching_difficulty}}
- 诊断依据：{{difficulty_diagnosis_basis}}
- 教学目标依据：{{difficulty_goal_basis}}

## 5. 分组建议

### 同质分组

#### {{homogeneous_group_name}}（{{homogeneous_group_id}}）

- 学生：{{students}}
- 共同特点：{{common_characteristics}}
- 进阶方向：{{progression_direction}}
- 适用活动：{{use_case}}

### 异质分组

仅在确有合作价值时呈现；否则说明不建议及理由。

选择与实际方案一致的一种格式，不同时输出两种。

#### 格式 A：各组完成同一任务

- 共同任务：{{shared_task}}
- 合作规则：{{shared_collaboration_rule}}
- 共同产出：{{shared_product}}

| 小组名称 | 学生 | 组内互补依据 |
|---|---|---|
| {{heterogeneous_group_name}} | {{students}} | {{group_specific_complementarity}} |

共同任务、合作规则和共同产出只能出现一次，不得在各组下重复。

#### 格式 B：共同主线下各组任务不同

- 共同合作规则：{{shared_collaboration_rule}}

| 小组名称 | 学生 | 任务重点 | 组内互补依据 |
|---|---|---|---|
| {{heterogeneous_group_name}} | {{students}} | {{group_task_focus}} | {{group_specific_complementarity}} |

任务重点必须具有实质差异；若只是措辞不同，应改用格式 A。

异质分组不得按 SOLO 层级机械配额。同质方案以及任何已提出的异质方案都必须分别覆盖所有参与诊断的学生；如果无法形成有价值的全员异质方案，写明不建议的理由，不强行凑组。

## 6. 后续干预设计依据

> 本部分向后续精准干预设计 Skill 传递决策依据，不展开具体课堂活动、支架、组织流程和形成性评价。

| 面向学生 | 诊断发现 | 需要促进的认知变化 | 后续设计需要满足的条件 |
|---|---|---|---|
| {{target_students}} | {{diagnostic_finding}} | {{intended_cognitive_change}} | {{design_requirements}} |

## 机器可读结果

在上述 Markdown 后附加：

```json solo_class_diagnosis
{
  "schema_version": "1.1",
  "class_summary": {},
  "overall_diagnosis": {},
  "level_analyses": [],
  "teaching_priorities": {},
  "grouping_recommendations": {},
  "intervention_design_basis": [],
  "traceability": {}
}
```

实际输出必须填写所有必需字段并通过 `class-diagnosis-output.schema.json`，不得保留占位符。
