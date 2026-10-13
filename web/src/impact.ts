import { exactInstant } from './exactTime';
import type { ResearchReading } from './types';

export interface ImpactTarget { project: string; revision: number; entityId?: string; versionId?: string; scope?: Record<string, string> | null; reading?: ResearchReading | null }
export interface ImpactOptions { candidates: boolean; reported: boolean }
export type ImpactCollection = 'affected' | 'paths';
export interface ImpactNode {
  node_id: string; layer: 'L1' | 'L2'; kind: string; scope: Record<string, string> | null;
  entity_id?: string; version_id?: string | null; run_id?: string | null; request_id?: string | null;
  content_claim_ids?: number[]; labels?: string[];
}
export interface ImpactItem extends ImpactNode { depth: number; impact_basis: 'confirmed_relation' | 'potential_dependency'; action: 'review_dependency' }
export interface ImpactPath { source: string; target: string; potential: boolean; relation: string; claim_id: number | null; edge_id: string | null; basis: string | null; scope: Record<string, string> | null }
export interface ImpactPage extends ResearchReading {
  seed: ImpactNode; scope: Record<string, string> | null; scope_filter: boolean;
  collection: ImpactCollection; items: (ImpactItem | ImpactPath)[]; total: number; offset: number; next_offset: number | null;
  affected_total: number; paths_total: number; traversal_complete: boolean; max_depth: number;
  include_candidates: boolean; include_reported: boolean; state_changes: false;
  actual_io_completeness: 'unknown'; l1_dependencies_complete: false; semantics: 'downstream_review_only';
}
export const impactLimit = 20;
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown, min = 0): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= min;
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
const scope = (v: unknown): boolean => v === null || object(v) && Object.values(v).every(item => typeof item === 'string');
function scopeKey(v: Record<string, string> | null | undefined): string { return JSON.stringify(v == null ? v ?? null : Object.entries(v).sort(([a], [b]) => a.localeCompare(b))); }
export function impactTargetKey(t: ImpactTarget): string { return JSON.stringify([t.project, t.revision, t.entityId, t.versionId, t.scope !== undefined, scopeKey(t.scope), t.reading?.occurred_until, t.reading?.known_until]); }
export function impactRequest(t: ImpactTarget, o: ImpactOptions, collection: ImpactCollection, offset = 0, reading?: ResearchReading | null): Record<string, string | number> {
  const fixed = reading ?? t.reading;
  return { project: t.project, expected_revision: t.revision,
    ...(t.entityId ? { entity_id: t.entityId } : { version_id: t.versionId! }),
    ...(t.scope !== undefined ? { scope: JSON.stringify(t.scope) } : {}),
    ...(fixed ? { occurred_until: fixed.occurred_until, known_until: fixed.known_until } : {}),
    include_candidates: String(o.candidates), include_reported: String(o.reported), collection, limit: impactLimit, offset };
}
function node(v: unknown): v is ImpactNode {
  return object(v) && text(v.node_id) && ['L1', 'L2'].includes(String(v.layer)) && text(v.kind) && scope(v.scope)
    && (v.layer !== 'L2' || text(v.entity_id) && Array.isArray(v.content_claim_ids) && v.content_claim_ids.every(id => integer(id, 1))
      && Array.isArray(v.labels) && v.labels.every(label => typeof label === 'string'));
}
function item(v: unknown): v is ImpactItem { return object(v) && node(v) && integer(v.depth, 1)
  && ['confirmed_relation', 'potential_dependency'].includes(String(v.impact_basis))
  && v.action === 'review_dependency' && (v.layer !== 'L1' || v.impact_basis === 'potential_dependency'); }
function path(v: unknown): v is ImpactPath { return object(v) && text(v.source) && text(v.target) && typeof v.potential === 'boolean'
  && ['supports', 'challenges', 'selects', 'join_input', 'consumes', 'produces', 'describes_attempt'].includes(String(v.relation))
  && (v.claim_id === null || integer(v.claim_id, 1)) && (v.edge_id === null || text(v.edge_id))
  && (v.basis === null || text(v.basis)) && scope(v.scope); }
export function parseImpactPage(raw: unknown, target: ImpactTarget, options: ImpactOptions, collection: ImpactCollection, offset = 0, fixed?: ResearchReading | null): ImpactPage {
  const broken = () => new Error('下游查询的对象、项目、范围、双时间、修订或分页不一致，未显示此响应。');
  const reading = fixed ?? target.reading;
  if (!object(raw) || raw.project_id !== target.project || raw.revision !== target.revision
    || exactInstant(raw.occurred_until) === null || exactInstant(raw.known_until) === null
    || reading && (exactInstant(raw.occurred_until) !== exactInstant(reading.occurred_until) || exactInstant(raw.known_until) !== exactInstant(reading.known_until))
    || !scope(raw.scope) || raw.scope_filter !== (target.scope !== undefined) || scopeKey(raw.scope as Record<string, string> | null) !== scopeKey(target.scope)
    || !node(raw.seed) || target.entityId && raw.seed.entity_id !== target.entityId || target.versionId && raw.seed.version_id !== target.versionId
    || raw.collection !== collection || raw.offset !== offset || !integer(raw.total) || !integer(raw.affected_total) || !integer(raw.paths_total)
    || raw.total !== (collection === 'affected' ? raw.affected_total : raw.paths_total)
    || !Array.isArray(raw.items) || raw.items.length !== Math.min(impactLimit, Math.max(0, raw.total - offset))
    || raw.items.some(value => !(collection === 'affected' ? item(value) : path(value)))
    || raw.next_offset !== (offset + impactLimit < raw.total ? offset + impactLimit : null)
    || typeof raw.traversal_complete !== 'boolean' || !integer(raw.max_depth, 1) || raw.max_depth > 64
    || raw.include_candidates !== options.candidates || raw.include_reported !== options.reported
    || raw.state_changes !== false || raw.actual_io_completeness !== 'unknown' || raw.l1_dependencies_complete !== false || raw.semantics !== 'downstream_review_only') throw broken();
  const keys = raw.items.map(value => collection === 'affected' ? (value as ImpactItem).node_id : JSON.stringify(value));
  if (new Set(keys).size !== keys.length) throw broken();
  return raw as unknown as ImpactPage;
}
export function appendImpactPage(previous: ImpactPage, next: ImpactPage): ImpactPage {
  if (next.offset !== previous.next_offset || next.collection !== previous.collection || next.total !== previous.total
    || next.affected_total !== previous.affected_total || next.paths_total !== previous.paths_total || next.traversal_complete !== previous.traversal_complete
    || next.max_depth !== previous.max_depth || next.include_candidates !== previous.include_candidates || next.include_reported !== previous.include_reported
    || next.project_id !== previous.project_id || next.revision !== previous.revision || next.occurred_until !== previous.occurred_until || next.known_until !== previous.known_until
    || scopeKey(next.scope) !== scopeKey(previous.scope) || next.scope_filter !== previous.scope_filter || JSON.stringify(next.seed) !== JSON.stringify(previous.seed)) throw new Error('下游查询分页条件已变化，请重新读取。');
  const items = [...previous.items, ...next.items];
  const ids = items.map(v => next.collection === 'affected' ? (v as ImpactItem).node_id : JSON.stringify(v));
  if (new Set(ids).size !== ids.length) throw new Error('下游查询分页重复，未合并。');
  return { ...next, items };
}
