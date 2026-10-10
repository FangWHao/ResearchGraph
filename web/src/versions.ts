import { healthCount } from './health';
import type { FileVersionRecord, VersionsPage } from './types';

export function objectFields(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
export function versionOrigin(version: FileVersionRecord) {
  if (version.source === 'agent_edit' || version.representation === 'tool_reported_utf8') {
    return { kind: 'reported', title: '工具报告文本', note: '工具报告的 UTF-8 文本，不能等同当时文件的物理字节；直接记录依据不代替审核确认。' } as const;
  }
  if (version.source === 'current_file' && version.algo === 'sha256' && version.representation === 'physical_file_bytes') {
    return { kind: 'current', title: '当前文件哈希观察', note: '完整 SHA256 对应实际读取窗口的当前普通文件；不等同发现快照当时的内容，大文件正文没有复制保存。' } as const;
  }
  if (version.source === 'shadow_snapshot' && version.algo === 'git-sha1') {
    if (version.representation === 'physical_file_bytes') return { kind: 'archived', title: '影子快照字节', note: '来自影子快照保存的文件字节；完整性与保存状态见观察记录，不能证明某次运行实际使用了此版本。' } as const;
    if (version.representation === 'symlink_target_bytes') return { kind: 'link', title: '快照中的链接目标字节', note: '保存的是链接目标文字的字节，不是链接所指文件的内容。' } as const;
  }
  return { kind: 'unknown', title: '版本来源或表示未知', note: '来源、算法与表示未提供一致信息，不能据此判断为完整快照或物理文件字节。' } as const;
}
export function exactPathError(value: string): string | null {
  if (value === '') return null;
  if (new TextEncoder().encode(value).length > 4096) return '路径超过 4096 UTF-8 字节。';
  if (!/^(?:\/|[A-Za-z]:[\\/]|\\\\)/.test(value)) return '请输入精确的绝对路径，或留空查看全部版本。';
  return null;
}
export function parseVersionsPage(value: unknown, project: string, offset: number, limit: number): VersionsPage {
  const fields = objectFields(value);
  if (!fields) throw new Error('文件版本接口返回的记录格式无效。');
  if (fields.offset !== undefined && fields.offset !== offset) throw new Error('文件版本返回的分页位置与当前请求不一致。');
  if (fields.limit !== undefined && fields.limit !== limit) throw new Error('文件版本返回的分页大小与当前请求不一致。');
  const versions = Array.isArray(fields.versions) ? fields.versions.map(item => {
    const record = objectFields(item) ?? {};
    if (record.project_id !== project) throw new Error('文件版本归属缺失或与当前项目不一致，本次结果不展示。');
    return record as FileVersionRecord;
  }) : undefined;
  if (versions && versions.length > limit) throw new Error('文件版本列表超过当前页上限。');
  return { revision: healthCount(fields.revision) ?? undefined, total: healthCount(fields.total) ?? undefined,
    offset: fields.offset as number | undefined, limit: fields.limit as number | undefined,
    partial: typeof fields.partial === 'boolean' ? fields.partial : undefined, versions };
}
export function versionsNextOffset(page: VersionsPage | null, offset: number): number | null {
  if (!page || !page.versions || page.offset !== offset) return null;
  const limit = healthCount(page.limit); const total = healthCount(page.total);
  if (limit == null || limit === 0 || total == null) return null;
  const next = offset + limit;
  return Number.isSafeInteger(next) && next < total ? next : null;
}
export function metadataBoolean(value: unknown): string {
  return value === true ? '是' : value === false ? '否' : '未知';
}
export function observationSignature(value: unknown): string {
  if (!Array.isArray(value) || !value.every(entry => typeof entry === 'number' && Number.isInteger(entry))) return '未知';
  if (!value.every(entry => Number.isSafeInteger(entry))) return '签名包含超出浏览器整数精度的字段，无法在此精确展示。';
  return JSON.stringify(value);
}
export function observationConflict(version: FileVersionRecord, observation: Record<string, unknown>): string | null {
  if (version.source === 'current_file' && observation.snapshot_id != null) return '当前文件观察错误关联快照；此关联不能作为快照证据。';
  if (typeof observation.version_id === 'string' && observation.version_id !== version.version_id) return '观察的版本身份不一致，不能作为该版本的证明。';
  if (observation.cache_reused === true && (typeof observation.cached_from !== 'string' || !observation.cached_from)) return '缓存复用缺少原观察标识，读取窗口来源不能核对。';
  return null;
}
export function observationListIssue(version: FileVersionRecord): string | null {
  if (!Array.isArray(version.observations)) return null;
  const returned = version.observations.length;
  if (returned > 3) return '接口返回超过 3 条观察，仅显示前 3 条；返回信息不符合观察上限。';
  const count = healthCount(version.observations_total);
  if (count == null) return '观察总数未知，不能用当前列表推断完整历史。';
  if (count < returned || (version.observations_partial === false && count !== returned) || (version.observations_partial === true && count <= returned)) return '观察条数、总数与完整性记录不一致，不能视为完整历史。';
  return null;
}
