# 平台三部分反馈呈现模板

以下由平台根据诊断JSON生成，不要求AI额外输出该Markdown。AI只返回 `solo_student_diagnosis` 和 `student_feedback_markdown`，不返回独立教师报告。

## 当前情况

### SOLO 水平

{{level}}（{{level_name}}）：{{rationale}}

### 判断依据

{{evidence_bullets_each_as_student_quote_then_this_shows}}

### 当前障碍

{{next_level_gap}}

{{main_obstacle}}

## 进阶目标

{{target_level_display}}（{{target_level_name}}）：{{concrete_goal}}

## 学习建议

1. **{{strategy_title}}**：针对{{identified_problem}}，在本次主题后续学习中，{{action}}。完成表现：{{observable_result}}。迁移使用：{{transfer_use}}

证据采用原话与通俗解释；保留2—4条具体策略。不要求思维结构图、状态符号或独立的学生信息章节。历史报告保留，不通过格式要求覆盖已有内容。
