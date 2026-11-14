> 本文件说明平台的展示结构。模型仅返回结构化 JSON；教师可读报告由平台排版，不另行生成。

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
- 典型依据：{{typical_student_brief_analysis}}
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

#### {{task_based_group_name}}

- 学情：{{common_characteristics}}
- 学习任务：{{learning_task}}
- 教学支持：{{progression_direction}}
- 第一组：{{students}}

相同任务只介绍一次，其下列出各组名单。名称从任务提炼核心活动，完整任务单独保留。当前平台仅展示同质分组。

## 6. 后续干预设计依据

> 本部分向后续精准干预设计 Skill 传递决策依据，不展开具体课堂活动、支架、组织流程和形成性评价。

| 面向学生 | 诊断发现 | 需要促进的认知变化 | 后续设计需要满足的条件 |
|---|---|---|---|
| {{target_students}} | {{diagnostic_finding}} | {{intended_cognitive_change}} | {{design_requirements}} |

## 输出约定

模型仅返回运行时 Schema 要求的完整 JSON，不输出本模板、占位符或附加 Markdown。统计和完整名单由程序核对补齐。
