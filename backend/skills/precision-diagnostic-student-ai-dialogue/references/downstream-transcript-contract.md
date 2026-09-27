# 下游完整对话契约

## 目的

本 Skill 生成的完整原始对话应可直接写入 `solo-student-diagnosis-feedback` 的 `human_ai_dialogue` 字段。

## 消息格式

```json
{
  "turn_id": "turn-0001",
  "role": "assistant",
  "content": "消息原文"
}
```

要求：

- `role` 只使用 `assistant` 或 `student`；
- `turn_id` 在一个会话内唯一并按真实顺序递增；
- `content` 保存当时实际显示或输入的完整原文；
- 不删除学生的空泛、重复、错误或越界表达；
- 不把内部执行记录写入对话；
- 不用AI改写后的版本替换学生原话。

## 证据污染控制

后续诊断只把学生自主表达作为学生证据。因此，每条AI消息都必须严格遵守答案边界；如果平台检测到AI误泄露关键内容，应在会话元数据中标记该轮，但不要修改已经发生的原始对话。

