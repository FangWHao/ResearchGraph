# 会话 1 的候选提取报告

本报告供人工复核。候选不代表已采用、已确认或科学结论成立。

## 候选 1 · entity_version

审核状态：candidate

```json
{
  "claim_type": "entity_version",
  "content": "方法甲，仅用于 data_v2 的 cnv 步骤",
  "kind": "approach",
  "label": "方法甲",
  "scope": {
    "stage": "cnv",
    "version": "data_v2"
  },
  "temp_id": "8696d629-84ed-41ef-a5e1-d9ffaf0ee0d1"
}
```

原文引用：

- 事件 1，UTF-8 字节 23–76

> 采用方法甲，仅用于 data_v2 的 cnv 步骤。

## 候选 2 · decision_event

审核状态：candidate

```json
{
  "action": "accepted",
  "claim_type": "decision_event",
  "explicitness": "explicit",
  "reason": "用户明确表示采用方法甲，并限定仅用于 data_v2 的 cnv 步骤",
  "referent_unique": true,
  "scope": {
    "stage": "cnv",
    "version": "data_v2"
  },
  "speaker": "user",
  "target": "8696d629-84ed-41ef-a5e1-d9ffaf0ee0d1"
}
```

原文引用：

- 事件 1，UTF-8 字节 23–76

> 采用方法甲，仅用于 data_v2 的 cnv 步骤。

## 候选 3 · decision_event

审核状态：candidate

```json
{
  "action": "deferred",
  "claim_type": "decision_event",
  "explicitness": "explicit",
  "reason": "用户明确暂缓方法甲，并保留原有阴性结果",
  "referent_unique": true,
  "scope": {
    "stage": "cnv",
    "version": "data_v2"
  },
  "speaker": "user",
  "target": "8696d629-84ed-41ef-a5e1-d9ffaf0ee0d1"
}
```

原文引用：

- 事件 2，UTF-8 字节 23–92

> 暂缓方法甲，原有阴性结果保留；方法乙暂不采用。

## 候选 4 · entity_version

审核状态：candidate

```json
{
  "claim_type": "entity_version",
  "content": "方法乙（原话仅提及暂不采用，内容未给出）",
  "kind": "approach",
  "label": "方法乙",
  "scope": {
    "stage": "unknown",
    "version": "unknown"
  },
  "temp_id": "73a1b74b-5fd4-41f0-bf92-25dad87ff9cf"
}
```

原文引用：

- 事件 2，UTF-8 字节 23–92

> 暂缓方法甲，原有阴性结果保留；方法乙暂不采用。

## 候选 5 · decision_event

审核状态：candidate

```json
{
  "action": "rejected",
  "claim_type": "decision_event",
  "explicitness": "explicit",
  "reason": "用户明确表示方法乙暂不采用",
  "referent_unique": true,
  "scope": {
    "stage": "unknown",
    "version": "unknown"
  },
  "speaker": "user",
  "target": "73a1b74b-5fd4-41f0-bf92-25dad87ff9cf"
}
```

原文引用：

- 事件 2，UTF-8 字节 23–92

> 暂缓方法甲，原有阴性结果保留；方法乙暂不采用。

## 覆盖与验收

- 待处理阶段记录：0。
- 人工参考决定对照：尚未提供；找回率、错误条数与复核耗时未测。
- 不能据此报告宣布 M1 通过。
