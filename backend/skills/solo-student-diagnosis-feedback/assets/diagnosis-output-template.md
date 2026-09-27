# 学生个体 SOLO 思维结构诊断与反馈

## 1. 学生信息

- 学生编号：{{student_id}}
- 学生姓名：{{student_name}}
- 精准教学主题：{{precision_teaching_topic}}
- 诊断任务：{{diagnostic_task}}

## 2. SOLO 诊断结果与依据

- 当前层级：{{level}}（{{level_name}}）：{{current_level_plain_meaning}}
- 判断说明：{{rationale}}
- 尚未达到下一层级的原因：{{next_level_gap}}

### 判断依据

{{evidence_bullets_each_as_student_quote_then_this_shows}}

{{multi_source_note_if_needed}}

## 3. 当前思维结构特征

### 思维结构图

`● 已形成　◐ 部分形成　○ 尚未表现`

```text
{{thinking_structure_text_map_with_5_to_9_nodes}}
```

### 简要分析

- **信息识别：** {{recognized_information_summary}}
- **关系建构：** {{relation_building_summary}}
- **整体组织：** {{overall_organization_summary}}
- **抽象迁移：** {{abstraction_transfer_summary}}

> 本部分合计约200个汉字，只分析学生在当前材料中实际表现出的思维结构。

## 4. 主要问题与进阶障碍

{{problem_bullets_2_to_4}}

- **主要进阶障碍：** {{main_obstacle}}

## 5. 进阶目标

- 当前层级：{{current_level}}（{{current_level_name}}）：{{current_level_plain_meaning}}
- 目标层级：{{target_level_display}}（{{target_level_name}}）：{{target_level_plain_meaning}}
- 具体目标：{{concrete_goal}}

## 6. 后续学习策略

1. **{{strategy_title}}**：针对{{identified_problem}}，在“{{precision_teaching_topic}}”后续学习中，{{action}}。完成表现：{{observable_result}}。迁移使用：{{transfer_use}}
2. **{{strategy_title}}**：针对{{identified_problem}}，在“{{precision_teaching_topic}}”后续学习中，{{action}}。完成表现：{{observable_result}}。迁移使用：{{transfer_use}}

## 机器可读结果

在上述 Markdown 后附加：

```json solo_student_diagnosis
{
  "schema_version": "1.0",
  "student": {},
  "evidence_sources": {},
  "diagnosis": {},
  "thinking_structure_summary": "",
  "progression": {},
  "strategies": [],
  "traceability": {}
}
```

实际输出必须填写所有必需字段，并通过 `diagnosis-output.schema.json`；不要保留占位符。
