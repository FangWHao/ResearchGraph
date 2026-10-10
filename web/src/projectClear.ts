export interface ClearPreview {
  project_id: string; revision: number; rows: Record<string, number>; objects: number;
  shared_objects_retained: number; managed_paths: number; blockers: string[]; boundary: string; preview_sha256: string;
}
export interface ClearRecord extends ClearPreview {
  state: 'pending' | 'complete'; request_id: string; started_at: string; finished_at?: string;
}
export type ClearStatus = { state: 'idle'; boundary: string } | ClearRecord;
export interface ClearIntent { project_id: string; request_id: string; preview_sha256: string }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('清除回执格式异常，请检查清除状态。');
  return value as Record<string, unknown>;
}
function count(value: unknown): value is number { return Number.isSafeInteger(value) && (value as number) >= 0; }
export function parseClearPreview(value: unknown, project?: string): ClearPreview {
  const v = object(value); const rows = object(v.rows);
  if (typeof v.project_id !== 'string' || !uuid.test(v.project_id) || (project && v.project_id !== project)
    || !count(v.revision) || !Object.values(rows).every(count) || !count(v.objects) || !count(v.shared_objects_retained) || !count(v.managed_paths)
    || !Array.isArray(v.blockers) || !v.blockers.every(b => typeof b === 'string') || typeof v.boundary !== 'string' || !v.boundary
    || typeof v.preview_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(v.preview_sha256)) throw new Error('清除预览的项目、数量或摘要异常，未启用执行。');
  return v as unknown as ClearPreview;
}
export function parseClearStatus(value: unknown, intent?: ClearIntent): ClearStatus {
  const v = object(value);
  if (v.state === 'idle' && typeof v.boundary === 'string' && v.boundary) return { state: 'idle', boundary: v.boundary };
  const preview = parseClearPreview(v);
  if ((v.state !== 'pending' && v.state !== 'complete') || typeof v.request_id !== 'string' || !uuid.test(v.request_id)
    || typeof v.started_at !== 'string' || !Number.isFinite(Date.parse(v.started_at))
    || (v.state === 'complete' && (typeof v.finished_at !== 'string' || !Number.isFinite(Date.parse(v.finished_at))))
    || (intent && (preview.project_id !== intent.project_id || v.request_id !== intent.request_id || preview.preview_sha256 !== intent.preview_sha256))) {
    throw new Error('清除回执与请求不一致，未认定清除完成，请检查清除状态。');
  }
  return v as unknown as ClearRecord;
}
export function clearIntent(preview: ClearPreview, project: string, name: string, confirmation: string, requestId: string): ClearIntent {
  if (preview.project_id !== project || !name || confirmation !== name || preview.blockers.length || !uuid.test(requestId)) throw new Error('请核对当前项目的无阻碍预览，并完整输入项目名称。');
  return { project_id: project, preview_sha256: preview.preview_sha256, request_id: requestId };
}
export function sameClearPreview(a: ClearPreview, b: ClearPreview): boolean {
  return a.project_id === b.project_id && a.preview_sha256 === b.preview_sha256;
}
