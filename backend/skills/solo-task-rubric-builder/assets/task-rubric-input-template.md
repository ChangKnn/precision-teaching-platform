# 任务特定 SOLO 量规建构输入

```yaml
precision_teaching_context:
  subject: 学科
  grade: 年级
  textbook_version_and_chapter: 教材版本与章节
  precision_teaching_topic: 精准教学主题
  precision_teaching_goals:
    - 精准教学目标1
  precision_teaching_content:
    - 精准教学内容1

diagnostic_task:
  task_text: 诊断任务原文
  materials:
    - 学生作答时实际看到的材料
  requirements:
    - 作答要求或限制

diagnostic_task_design_intent: 必填，为什么选择这个任务、希望观察哪些思考过程；不增加学生未被告知的要求

```

## 使用说明

- `precision_teaching_context`、`diagnostic_task` 和 `diagnostic_task_design_intent` 为必需输入；
- 量规构建不读取或考虑学生回答；
- 通用 SOLO 量规由 Skill 内置知识库提供，不作为运行时输入；
- 教学情境不能替代任务原文，也不能改变内置通用 SOLO 量规；
- 如果教学目标没有被诊断任务实际诱发，应报告任务缺口，不得把目标直接写成量规判据。
