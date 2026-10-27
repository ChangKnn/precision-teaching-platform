# 机器可读契约与教师表达分离

## 兼容输出

继续使用 assets/task-specific-solo-rubric.schema.json 的 schema_version 1.1，不改下游字段名称。教师版使用任务量规表；结构化接口仅接收 JSON 时不输出额外 Markdown。

source_basis 固定记录内置通用 SOLO 来源与五个锚点；teacher_context_used 记录实际使用的 precision_teaching_context 字段，teacher_design_intent_used 记录是否参考教师意图。source_uncertainties 说明材料或意图与任务观察机会不一致，不把教师假设当学生事实。

输入 diagnostic_task_design_intent 为必需且非空的诊断任务设计意图。输出 teacher_design_intent_used 仅保留为兼容字段名；成功建构时应为 true，不代表意图是可选输入。

## 最小内部任务分析

task_analysis 只需记录认知要求、必要内容及其联系、任务诱发能力。保留既有键以兼容下游，但不能把字段存在误解为必须穷举：

- true_cognitive_demand：一句话说明核心认知活动。
- elements：只保留区分层级所需的关键内容；满足 Schema 的至少一个真实对象，不凑固定数量。
- relations.items：只保留关键联系，可为空；whole_structure 用简短说明呈现整体要求。若任务无法提供整体理解的观察机会，在任务限制中解释。
- relations.alternative_pathways：可省略或为空；多种有效表达不必逐一编号。
- abstractions：没有合理拓展机会时可为空，不为填结构而编造高阶任务。
- task_affordance：真实记录任务主动提供的观察机会、限制与可选建议；highest_reasonably_elicitable_level 仅描述预计观察机会，不是学生定级上限。

定位编号仍可使用 E1、R1、A1、PATH-1 等，但仅留内部对象的 id、element_ids、relation_ids、requires_relation_ids、pathway_ids 和 trace_to 中。内部对象的含义也尽量用清楚的自然语言，不以编号代替解释。

## 教师可见字段

levels 各级的 core_performance、decision_evidence、adjacent_boundaries、typical_expressions、limitations 都必须独立可读，不得包含 E1、E2、R1、A1、PATH-1、G-R 等内部代码或简称式引用。

- core_performance：本任务中的主要认知特征。
- decision_evidence：具体、可观察的学生表现与必要理由，不写“使用某些编号即达标”。
- adjacent_boundaries：用自然语言解释相邻层级差异。
- typical_expressions：每级至少一条贴合本任务的非穷尽假想学生表达；不是关键词表，不直接评判真实学生。
- limitations：任务观察机会限制，用教师能直接理解的文字。
- P/U/M/R/EA 是 SOLO 层级名称，可正常出现。

新生成的五级 status 均为 defined，core_performance、decision_evidence、typical_expressions 均为非空文本列表。Schema 兼容旧数据的 not_reasonably_elicitable 和教师未完成草稿，但生成校验及教师确认不允许用该旧状态跳过三栏完整性要求。观察机会限制只放 limitations 等备注字段，不替代层级的可观察标准。

## 追溯与下游使用

每级 trace_to 至少包含对应通用量规锚点。若判据确实使用内部任务对象，再引用实际存在的对象；无需为每一级强行引用要素、关系或抽象编号。尤其不要为任务无法观察的 EA 编造抽象对象。

下游诊断以学生自主表达、具体任务和已确认的 decision_evidence、adjacent_boundaries 判断结构；trace_to 只作机器定位，不是教师或学生需要阅读的理由。接纳功能、关系等价的不同表达，而非逐字匹配示例。

交付前检查 JSON 可解析、编号引用存在、教师版和 JSON 的层级含义一致，以及五级定义忠于内置通用量规。任务观察机会不足不代表学生能力不足；证据不足也不是 P。
