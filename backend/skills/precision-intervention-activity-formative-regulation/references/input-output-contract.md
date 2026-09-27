# 输入输出契约

## 1. 上游输入

### `precision_teaching_context`

与诊断 Skill 和 Skill 1 共用的精准教学背景，包括学科、年级、教材版本与章节、精准教学主题、目标和内容。

### `diagnosis_bundle`

至少包含班级总人数、P/U/M/R/EA 分布、学生—层级映射和班级诊断。可附任务特定量规与个体诊断。只读取，不重新定级。

### `teacher_instructional_context`

统一包含：

- `teaching_content_and_curriculum_analysis`；
- `teaching_focus_and_difficulty_analysis`；
- `planned_duration_minutes`；
- `teaching_environment_and_ai_support_conditions`；
- 可选的 `teacher_lesson_conception`。

前两项为AI辅助生成并经教师确认的文本，教学时长和教学环境与AI支持条件由教师填写；课堂教学设计构想由教师选填。班级人数从班级诊断读取。

### `goal_path_design`

来自 Skill 1，`schema_version` 为 `1.1`，并满足：

- `design_status = teacher_confirmed`；
- `teacher_confirmation.status = teacher_confirmed`。

至少包含共同核心目标、分层进阶目标、学生—目标对应关系，以及主要路径的阶段、活动单元和时长。

### `teacher_activity_requirements`（可选）

可包含指定材料、必须保留的活动、禁止方式、AI 可用情况、AI 使用限制和教师补充说明。

## 2. 不可变约束

Skill 2 必须保持以下字段与 Skill 1 一致：

- 学生 SOLO 层级与学生编号；
- 目标编号及学生—目标对应关系；
- 主路径阶段编号及顺序；
- 上游活动单元编号；
- 各单元的路径功能、目标学生与目标；
- 各阶段时长和总时长。

Skill 1 中的 C/H/X/I 可作为组织建议；A（综合应用）和 S（学习站）只表示活动功能或路径安排。Skill 2 必须根据具体活动，从全班集体干预、同质小组干预、异质小组干预、个体干预中确定实际组织形式。

如果教师选择 Skill 1 的备选路径，编排智能体应先让 Skill 1 生成并确认该路径的阶段结构，再调用本 Skill。

## 3. 活动映射规则

- 每个上游活动单元都必须被一个下游活动覆盖。
- 一个阶段只有一个单元时，通常一对一生成活动。
- 同一阶段存在多个同时开展的活动单元时，将这些单元合并为一项教师端活动；`source_unit_ids` 保存全部单元编号，`parallel_group_tasks` 分别保存各组任务。
- 不得把并行单元分别编号成教师端的连续活动。
- 活动编号格式：`ACT-{stage_id}-{sequence}`，例如 `ACT-ST2-1`；`sequence_within_stage` 表示该阶段内真实的先后顺序。
- 一个单元若拆成多个连续子活动，覆盖该单元的活动时长之和必须等于所在阶段分配给它的时长。
- 一个活动覆盖多个并行单元时，活动时长等于阶段时长，不按小组数量累加。
- 活动及其 `parallel_group_tasks` 不能引用对应上游单元之外的目标或学生。

## 4. 输出

输出 `activity_formative_design`，`schema_version = 1.3`，包括：

- `activity_sequence_summary`：阶段、时长、活动编号和并行关系；
- `activities`：教师端顺序活动的完整机器字段；同一阶段的并行单元合并为一项活动，并通过 `source_unit_ids` 和 `parallel_group_tasks` 保留分组映射；`organization_forms` 和 `dialogue_circles` 只使用规定枚举；
- `formative_evaluation_nodes`：1—2次关键形成性评价的证据、标准和结果处理；多课时方案通常按每课时1—2次；
- `teacher_confirmation`：待确认问题与状态；
- `handoff`：供后续干预评价 Skill 和完整方案整合 Skill 使用的信息。

教师确认前，`design_status` 与 `teacher_confirmation.status` 均为 `pending_teacher_confirmation`；教师明确确认后，两者同时改为 `teacher_confirmed`。

### 教师端活动表

- 活动名称位于表格上方，括号内显示时长和组织形式；
- 表格不设置统一表头，只保留活动目标、活动过程、学习材料与资源、学习产出四个一级项目；
- 教师活动、学生活动、AI辅助仅作为活动过程内部的三个二级栏目；同伴行动归入学生活动；
- 活动目标从上游共同核心目标或分层进阶目标中选择并操作化，标注对应SOLO层级或进阶方向；
- 会话圈、主体行动顺序和完整上游映射保留在机器JSON中，不在教师表中另设栏目。

## 5. 下游边界

- 干预评价 Skill 使用目标、学习产出和形成性评价证据来设计干预结束后的评价。
- 完整方案整合 Skill 校核各 Skill 结果的一致性并生成完整方案。
- 本 Skill 不提前完成上述工作。
