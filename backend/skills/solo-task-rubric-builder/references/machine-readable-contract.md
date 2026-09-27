# 机器可读输出契约

## 1. 双重输出

先输出教师可读 Markdown，再输出一个有效 JSON 代码块，并把代码块标记为 `json solo_task_rubric`。JSON 必须符合 `assets/task-specific-solo-rubric.schema.json`。

不要在 JSON 中写注释、尾随逗号或 Markdown。未知值使用 `null`，不要用空字符串伪装已知信息。

## 2. 稳定编号

- 通用量规锚点：`G-P`、`G-U`、`G-M`、`G-R`、`G-EA`；
- 核心信息单元：`E1`、`E2`……；
- 关键推理关系：`R1`、`R2`……；
- 抽象拓展方向：`A1`、`A2`……；
- 替代路径：`PATH-1`、`PATH-2`……。

编号在同一输出中必须唯一。教师后续修改文字时尽量保持编号不变；只有对象含义改变时才新建编号。

## 3. 关键字段语义

- `source_basis.general_rubric_source`：固定记录 Skill 内置 `references/general-solo-rubric.md` 及其原始来源《0912 SOLO 诊断量规》；
- `source_basis.general_rubric_anchors`：忠实写入内置量规的 G-P、G-U、G-M、G-R、G-EA，不补充外部定义；
- `source_basis.teacher_context_used`：为兼容量规 schema 1.1 保留的既有字段名；内容记录本次实际采用的 `precision_teaching_context` 字段名称，不表示运行时仍使用旧输入名 `teacher_context`；
- `task_analysis.elements`：核心信息单元 E；每项只包含编号、名称、含义和可选路径，不包含“重要性”；
- `task_analysis.relations.items`：关键推理关系 R；每条 `relation_statement` 必须显式表达关系方向或关系动作，并引用至少一个相关 E。关系可以把一个 E 连接到任务结论，也可以连接多个 E；
- `task_analysis.relations.whole_structure`：R 层内部的整体解释功能，不再作为独立分析层；
- `task_analysis.relations.alternative_pathways`：可选的有效作答路径；
- `task_analysis.abstractions`：只有在 R 基础上成立的 A；
- `task_analysis.task_affordance`：任务合理可诱发的最高层级、限制和可选改写建议；
- `levels.*.status`：`defined` 或 `not_reasonably_elicitable`；
- `levels.*.trace_to`：判据的规范与任务结构来源；
- `usage_notes.evidence_sufficiency`：提醒下游先判断证据是否足够，不能把证据不足当 P。

## 4. 下游使用规则

`solo-student-diagnosis-feedback` 应使用 `decision_evidence`、`adjacent_boundaries` 和 `trace_to` 判断结构，不应把 `typical_expressions` 当作关键词表。学生出现量规之外但结构等价的表达时，应按 E、R、A 的功能等价性判断，而不是按字面匹配。

若某一级为 `not_reasonably_elicitable`，下游不得因学生未展示该级而做否定判断；应报告“当前任务无法提供足够机会观察该层级”。

## 5. 一致性检查

交付前检查：

1. Markdown 与 JSON 的任务概括、E、R（含整体解释结构）、A 和五层级含义一致；
2. Markdown 已呈现精准教学情境和目标—任务对应关系，JSON 的 `teacher_context_used` 已记录实际采用的 `precision_teaching_context` 字段；
3. JSON 可被标准解析器解析；
4. 所有 `trace_to` 编号真实存在；
5. EA 的 `trace_to` 至少包含 `G-EA`、一个 R 和一个 A；
6. R 的 `trace_to` 至少包含 `G-R`、任务所需的关键 E 和一个 R，且判据与 `relations.whole_structure` 一致；
7. `typical_expressions` 明确是非穷尽示例。
