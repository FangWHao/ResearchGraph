# 会话 1 的候选提取报告

本报告供人工复核。候选不代表已采用、已确认或科学结论成立。

## 候选 1 · entity_version

审核状态：candidate

```json
{
  "claim_type": "entity_version",
  "content": "方法甲",
  "kind": "approach",
  "label": "方法甲",
  "scope": {
    "analysis_step": "cnv",
    "dataset_version": "data_v2"
  },
  "temp_id": "3b5d4ae4-85be-4614-b648-52a6c90d9b79"
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
  "reason": "用户明确表示采用该方法，且限定用于 data_v2 的 cnv 步骤",
  "referent_unique": true,
  "scope": {
    "analysis_step": "cnv",
    "dataset_version": "data_v2"
  },
  "speaker": "user",
  "target": "3b5d4ae4-85be-4614-b648-52a6c90d9b79"
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
  "reason": "用户表示暂缓方法甲，原有阴性结果保留",
  "referent_unique": true,
  "scope": {
    "analysis_step": "cnv",
    "dataset_version": "data_v2"
  },
  "speaker": "user",
  "target": "3b5d4ae4-85be-4614-b648-52a6c90d9b79"
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
  "content": "方法乙（在 data_v2 的 cnv 步骤中暂不采用）",
  "kind": "approach",
  "label": "方法乙",
  "scope": {
    "analysis_step": "cnv",
    "dataset_version": "data_v2"
  },
  "temp_id": "1b3e4aa2-00e0-4f0c-a99c-f2ba858287db"
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
    "analysis_step": "cnv",
    "dataset_version": "data_v2"
  },
  "speaker": "user",
  "target": "1b3e4aa2-00e0-4f0c-a99c-f2ba858287db"
}
```

原文引用：

- 事件 2，UTF-8 字节 23–92

> 暂缓方法甲，原有阴性结果保留；方法乙暂不采用。

## 覆盖与验收

- 待处理阶段记录：0。
- 人工参考决定对照：尚未提供；找回率、错误条数与复核耗时未测。
- 不能据此报告宣布 M1 通过。
