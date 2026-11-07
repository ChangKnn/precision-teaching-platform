---
name: solo-task-rubric-builder
description: 依据内置通用SOLO标准和当前任务直接生成五级教师量规，不输出任务拆解或编号。
metadata:
  short-description: 任务对应的五级SOLO标准
  version: 1.5.0
---
# 任务特定SOLO标准构建

先读取 references/general-solo-rubric.md。只构建标准，不读取学生回答或诊断学生。
`precision_teaching_context`：必需，提供教学背景；完整诊断任务和教师设计意图也必需。材料缺失或条件存在疑点时不得补造。

## 1. 理解任务
明确学生要完成什么，什么样的回答体现理解。内部理解即可，无需输出要素、关系、抽象方向、作答路径或任务拆解。

## 2. 生成五级量规
直接生成P、U、M、R、EA五级，均填写主要认知特征core_performance、学生表现decision_evidence、示例typical_expressions。每栏使用非空文字列表。示例用贴合任务的假想学生原句，不是唯一答案；接纳不同合理解法。
P尚未形成相关有效理解，U处理一个相关方面，M处理多个方面但主要并列，R形成相互联系的完整解释，EA在整体理解基础上概括、迁移或辨析适用边界。
不按字数、术语数、步骤数划级。五级完整，不能用“任务不要求”代替标准。任务未主动要求迁移，不等于学生不能自主展示迁移。

## 3. 检查区分度
重点检查单一方面、多个方面并列、形成整体、进一步概括或迁移之间的区别。相邻边界融入学生表现，避免重复字段。表述必须具体，不能只换名词套通用定义。

## 输出
只输出levels和notes。levels包含P/U/M/R/EA，每级仅有上述三栏；必要时notes用一两句说明任务观察机会的限制，不限制学生可判定层级。无明确限制时notes为空列表。
禁止输出task_analysis、要素关系路径清单、trace_to、内部编号、来源或版本；固定信息由程序填充。完整输出格式见assets/rubric-generation.schema.json。
