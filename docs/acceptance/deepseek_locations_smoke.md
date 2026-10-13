# 会话 1 的候选提取报告

本报告供人工复核。候选不代表已采用、已确认或科学结论成立。

## 候选 1 · entity_version

审核状态：candidate

```json
{
  "claim_type": "entity_version",
  "content": "方法甲，声明仅用于 data_v2 的 cnv 步骤",
  "kind": "approach",
  "label": "方法甲",
  "scope": {
    "analysis_step": "cnv",
    "dataset_version": "data_v2"
  },
  "temp_id": "af84b99d-3c1d-4abd-a653-543ad6df2746"
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
  "reason": "用户明确表示采用方法甲，限定于 data_v2 的 cnv 步骤",
  "referent_unique": true,
  "scope": {
    "analysis_step": "cnv",
    "dataset_version": "data_v2"
  },
  "speaker": "user",
  "target": "af84b99d-3c1d-4abd-a653-543ad6df2746"
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
  "reason": "用户暂缓方法甲，原有阴性结果保留",
  "referent_unique": true,
  "scope": {
    "analysis_step": "cnv",
    "dataset_version": "data_v2"
  },
  "speaker": "user",
  "target": "af84b99d-3c1d-4abd-a653-543ad6df2746"
}
```

原文引用：

- 事件 2，UTF-8 字节 23–68

> 暂缓方法甲，原有阴性结果保留；

- 事件 1，UTF-8 字节 23–76

> 采用方法甲，仅用于 data_v2 的 cnv 步骤。

## 候选 4 · entity_version

审核状态：candidate

```json
{
  "claim_type": "entity_version",
  "content": "方法乙，在 data_v2 的 cnv 步骤中暂不采用",
  "kind": "approach",
  "label": "方法乙",
  "scope": {
    "analysis_step": "cnv",
    "dataset_version": "data_v2"
  },
  "temp_id": "d2b47746-54a5-4d91-a315-f8a116234291"
}
```

原文引用：

- 事件 2，UTF-8 字节 68–92

> 方法乙暂不采用。

## 候选 5 · decision_event

审核状态：candidate

```json
{
  "action": "rejected",
  "claim_type": "decision_event",
  "explicitness": "explicit",
  "reason": "用户表示方法乙暂不采用",
  "referent_unique": true,
  "scope": {
    "analysis_step": "cnv",
    "dataset_version": "data_v2"
  },
  "speaker": "user",
  "target": "d2b47746-54a5-4d91-a315-f8a116234291"
}
```

原文引用：

- 事件 2，UTF-8 字节 68–92

> 方法乙暂不采用。

## 覆盖与验收

- 待处理阶段记录：0。
- 人工参考决定对照：尚未提供；找回率、错误条数与复核耗时未测。
- 不能据此报告宣布 M1 通过。
