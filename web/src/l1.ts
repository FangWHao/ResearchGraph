import type { ArtifactVersion, L1Edit } from './types';

export function eventNumber(value: string): number | null {
  const normalized = value.trim();
  if (!/^[1-9][0-9]*$/.test(normalized)) return null;
  const number = Number(normalized);
  return Number.isSafeInteger(number) ? number : null;
}

export function runStateText(state: string, exitCode: number | null): string {
  if (state === 'requested') return '已请求';
  if (state === 'started') return '已有启动记录';
  if (state === 'exited') return `已结束 · 退出码 ${Number.isSafeInteger(exitCode) ? exitCode : '未知'}`;
  return '运行状态未知';
}

export function editPresentation(edit: L1Edit) {
  const diff = edit.diff;
  if (!diff.available || typeof diff.text !== 'string') {
    return { kind: 'unavailable', title: '差异暂不可展示', text: null, reason: diff.reason } as const;
  }
  if (diff.format === 'patch_only') {
    const known = edit.before_version && edit.after_version ? '已登记编辑前后候选版本；'
      : edit.before_version ? '已登记编辑前候选版本，编辑后完整版本未知；'
      : edit.after_version ? '已登记编辑后候选版本，编辑前完整版本未知；' : '编辑前后完整版本未知；';
    return { kind: 'patch_only', title: edit.before_version || edit.after_version ? '工具报告补丁 · 候选版本已登记' : '仅有补丁', text: diff.text, reason: `${known}当前正文仅为补丁，不能代表完整文件内容。` } as const;
  }
  if (diff.format === 'reported_versions' && diff.complete_versions === true && edit.before_version && edit.after_version) {
    return { kind: 'reported_versions', title: '工具报告版本差异 · 待复核', text: diff.text, reason: '编辑前后是工具报告的候选 UTF-8 文本，不证明当时文件原始字节完全一致。' } as const;
  }
  if (diff.format === 'reported_before' && diff.complete_versions === false && edit.before_version && !edit.after_version) {
    return { kind: 'reported_before', title: '工具报告编辑前全文 · 待复核', text: diff.text, reason: '编辑前是工具报告的候选 UTF-8 全文，编辑后完整版本未知；没有编辑后版本不等于空文件，不证明文件已实际删除或保存字节一致。' } as const;
  }
  if (diff.format === 'reported_after' && diff.complete_versions === false && edit.after_version && !edit.before_version) {
    return { kind: 'reported_after', title: '工具报告编辑后全文 · 待复核', text: diff.text, reason: '编辑后是工具报告的候选 UTF-8 全文，编辑前完整版本未知；不证明编辑前文件不存在或实际保存字节一致。' } as const;
  }
  return { kind: 'unavailable', title: '差异完整性未证实', text: null, reason: '接口没有提供一致的前后版本与表示信息，无法据此核对完整版本。' } as const;
}

export function versionMetadata(version: Partial<ArtifactVersion>) {
  const phase = version.phase === 'before' ? '编辑前' : version.phase === 'after' ? '编辑后' : version.phase === 'observed' ? '文件观察' : '阶段未知';
  const review = version.claim_state === 'candidate' ? '待复核'
    : version.claim_state === 'confirmed' ? '已确认'
    : version.claim_state === 'dismissed' ? '已驳回' : '审核状态未知';
  const basis = version.basis === 'direct_record' ? '直接记录'
    : version.basis === 'manual' ? '人工记录'
    : version.basis === 'model_inference' ? '模型推断'
    : version.basis === 'time_match' ? '时间匹配' : '依据未知';
  const representation = version.representation === 'tool_reported_utf8' ? '工具报告的 UTF-8 文本'
    : version.representation === 'physical_file_bytes' ? '物理文件字节'
    : version.representation === 'symlink_target_bytes' ? '链接目标字节' : '版本表示未知';
  return { phase, review, basis, representation };
}

const gapNames: Record<string, string> = {
  project_unassigned: '原文尚未归属项目', call_not_recorded: '尚未找到对应请求记录',
  rg_context_in_tool_payload: '工具记录中的 ResearchGraph 注入内容已排除，不作为运行或编辑证据',
  unsupported_tool_fact: '此工具记录类型尚未生成运行或编辑映射',
  execution_metadata_missing: '缺少执行器退出或会话元数据',
  invalid_exit_code: '退出码格式无效', conflicting_exit_code: '执行器报告的退出码冲突',
  invalid_executor_session: '执行器会话标识无效', ambiguous_tool_metadata: '多条工具结果无法唯一关联执行信息',
  ambiguous_call_id: '调用标识对应多个请求，关联存在歧义',
  conflicting_execution_facts: '运行观测相互冲突，状态需要核对',
  cwd_root_unknown: '工作目录与项目根目录的归属未知',
  executor_session_unknown_or_ambiguous: '执行器会话归属未知或存在歧义',
  preimage_unknown: '编辑前完整版本未知', reported_preimage_missing: '工具未报告编辑前完整文本',
  postimage_unknown: '编辑后完整版本未知；没有编辑后版本不等于空文件',
  native_patch_failed: '原生补丁结束记录报告失败，不能据此证明编辑后版本',
  native_patch_metadata_conflict: '原生补丁请求与结果字段存在矛盾，不能证实成功或完整版本',
  native_patch_input_missing: '只有原生补丁报告，未记录原始自定义工具或函数补丁请求；工具报告不证明实际保存文件字节',
  native_patch_changes_missing: '原生补丁缺少可核对的结构化变更报告，不能证实应用结果',
  patch_success_not_proven: '补丁应用结果未被工具记录证实',
  artifact_root_unknown: '文件与项目根目录的归属未知',
  tool_error_after_unknown: '工具报告错误，编辑后版本未知',
  user_modified: '工具报告用户同时修改，版本归属需要核对',
  reported_edit_disagrees_with_request: '请求文本与工具报告结果不一致',
  reported_edit_request_unsupported: '编辑请求表示暂不支持，无法据此核定完整文件版本',
  reported_content_invalid_utf8: '工具报告内容不能有效表示为 UTF-8 文本',
  reported_content_over_limit: '工具报告内容超过保存上限',
  newline_boundary_unknown: '文件换行边界未知', mixed_newlines_unknown: '混合换行表示无法完整核定',
  structured_patch_missing: '缺少结构化补丁', invalid_structured_patch: '结构化补丁字段无效',
  structured_patch_preimage_mismatch: '补丁与工具报告的编辑前文本不匹配',
  structured_patch_position_mismatch: '补丁行位置不一致', overlapping_structured_patch: '补丁范围重叠',
  unsupported_structured_patch: '结构化补丁表示无法完整核定', unsupported_patch_format: '补丁格式暂不支持',
};

export function gapText(gap: string): string {
  return gapNames[gap] ?? '记录存在证据缺口，需查看原文核对';
}
