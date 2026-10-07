---
name: teacher-custom-analysis
description: 根据教师自定义的分析标准，对已提交的学生原始作答生成个人或班级补充反馈。用于精准教学诊断结果反馈；不替代 SOLO 定级，也不参与分层干预的自动决策。
metadata:
  short-description: 教师自定义标准的证据化反馈
  version: 1.0.0
---

# 教师自定义分析

输入提供教师原文标准、诊断任务和学生原始证据。`mode` 为 `individual` 时只分析一名学生；为 `class` 时分析输入的全班已提交学生。严格围绕教师标准，不自行改写为 SOLO 层级、分数或固定能力标签。与现有 SOLO 诊断并行，绝不改动其层级、量规或报告。

只把学生本人发言或提交文本作为判断证据。AI 的回复仅可帮助理解上下文，不可当作学生已掌握的内容。图片或附件未转写，不得假装已读取。每条证据必须给出输入中真实存在的 `source_id` 和逐字摘录；如果证据不足，明确写入 `limitations`，不虚构表现。班级反馈只能概括输入中的学生，不能把未提交者当作已分析对象，也不能凭少数例子声称全班一致。

输出遵守 `assets/custom-analysis-output.schema.json`。`findings` 可按教师标准拆为若干可读要点，每项写清判断与依据；`suggestions` 应与发现相连，保持可操作和审慎。`analyzed_student_count` 必须等于输入中的学生人数。不要输出 Markdown、内部来源编号解释或额外字段。
