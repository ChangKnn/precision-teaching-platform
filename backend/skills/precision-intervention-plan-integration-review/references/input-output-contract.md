# 输入输出契约

本 Skill 当前输入、输出契约版本均为 `1.2`。本版本兼容Skill 1 v1.2，并采用教案式学习活动表。

## 输入版本

| 来源 | 版本 | 确认要求 |
| --- | --- | --- |
| `solo-class-diagnosis-intervention` | `1.0` 或 `1.1` | 直接读取，不重新定级；输出标记真实输入版本 |
| `precision-intervention-goal-path-design` | `1.2` | `teacher_confirmed` |
| `precision-intervention-activity-formative-regulation` | `1.3` | `teacher_confirmed` |

遇到其他版本时先核对字段兼容性；不得假定字段语义未变。

## 输入字段

完整结构见 [../assets/plan-integration-input.schema.json](../assets/plan-integration-input.schema.json) 和 [../assets/plan-integration-input-template.yaml](../assets/plan-integration-input-template.yaml)。

### `precision_teaching_context`

精准教学背景，固定字段为：

- `subject`
- `grade`
- `textbook_version_and_chapter`
- `precision_teaching_topic`
- `precision_teaching_goals`
- `precision_teaching_content`

该对象不包含教学内容与课标分析、教学重难点或课堂条件。

### `teacher_instructional_context`

AI辅助生成并由教师确认、补充的“教学情境与条件”，固定字段为：

- `teaching_content_and_curriculum_analysis`：教学内容与课标分析；
- `teaching_focus_and_difficulty_analysis`：教学重点与难点分析；
- `planned_duration_minutes`：教学时长，也是审核时间的唯一基准；
- `teaching_environment_and_ai_support_conditions`：教学环境、AI可用方式、关键资源和现实限制；
- `teacher_lesson_conception`：教师对课堂教学设计的构想，可为空；仅供上游设计和内部审核使用，不在教师版完整方案中展示。

前两个分析字段由AI依据精准教学基本信息和班级诊断辅助生成，教师修改确认后传入；教学时长、教学环境与AI支持条件由教师填写；课堂构想由教师选填。班级人数从班级诊断读取。

未提供的设备、材料或 AI 条件一律视为不可依赖，不得自行假设。

### `diagnostic_task_context`

诊断任务信息，独立于两类背景对象：

- `task_id`：任务标识；
- `task_title`：可选标题；
- `task_text`：教师设计的诊断任务原文；
- `task_materials_summary`：可选材料摘要；
- `response_mode`：学生作答方式；
- `task_requirements`：任务要求；
- `source_ref`：平台内部地址或其他可访问引用，可为空。

机器输入继续保留上述结构，便于追溯和校验；教师版不逐项展示这些字段。生成方案时，将上游“诊断任务设计”的教师可读内容完整合并到一个“诊断任务”区块中，保留其中已有的题干、材料、作答说明和来源链接。`source_ref` 为空时不生成链接，也不单列“原任务与材料”。

### `class_diagnosis_result`

读取五层分布、各层典型表现和主要障碍、班级总述及教学重难点。不要使用这些信息重新判断个体层级。

### `goal_path_design`

读取共同目标、分层目标、学生目标归属、路径阶段、节点和时长。教师端不显示目标追溯字段，但机器检查保留 `goal_id`、`student_id`、`stage_id` 和 `unit_id`。

### `activity_formative_design`

读取活动、并行分组任务、学习产出和形成性评价节点。Skill 3 可以压缩表述，但不能改变实质设计。

教师端活动标题写为“活动X：名称（时长；组织形式）”。活动表只保留活动目标、活动过程、学习材料与资源、学习产出；活动过程内部按教师活动、学生活动、AI辅助三列呈现。会话圈保留在上游机器数据中用于审核，不进入教师端独立栏目。

## 输出字段

完整结构见 [../assets/plan-integration-output.schema.json](../assets/plan-integration-output.schema.json)。

### `integration_status`

- `ready_for_use`：八项均通过；
- `ready_with_suggestions`：没有需要修改项，但存在建议优化项；
- `needs_revision`：至少一项需要修改。

### `integrated_plan`

教师版方案的结构化数据，顺序为：基本信息、学生诊断结果、教学情境与条件、目标、路径、活动、形成性评价。

学生诊断结果中的总述必须覆盖：分布与差异、已有基础、共同与分层障碍、下一步教学方向。分层进阶目标后必须保留固定的“最近发展目标”说明。

### `audit_summary`

只包含八类审核结果。`review_items` 必须按 R1—R8 顺序各出现一次。

### `formatting_adjustments`

只记录不改变教学含义的整理，如统一术语、去重和表格化。不得记录实质修改。

## 失败与回退

| 问题来源 | 返回位置 |
| --- | --- |
| 学生层级、人数或名单不一致 | 班级聚合诊断 Skill |
| 目标、学生归属或路径问题 | Skill 1 |
| 活动、支架、会话圈、时长或形成性评价问题 | Skill 2 |
| 教学环境、AI 条件、资源或现实限制不明确 | 教师补充 |
