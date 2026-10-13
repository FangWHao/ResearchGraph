import { exactInstant } from './exactTime';
import type { Claim, EvidenceData, EvidenceTarget, ResearchGraphData, ResearchReading } from './types';

export interface ResearchDraft { occurredUntil: string; knownUntil: string }
export interface ResearchPage extends ResearchReading {
  scope: null; scope_filter: false; claims_total: number; total: number; offset: number;
  next_offset: number | null; items: Claim[];
}
export function emptyResearchDraft(): ResearchDraft { return { occurredUntil: '', knownUntil: '' }; }
export function researchIntent(project: string, draft: ResearchDraft): string {
  return JSON.stringify([project, draft.occurredUntil, draft.knownUntil]);
}
const broken = () => new Error('研究图响应的项目、双时间、修订或分页不一致，未混入旧图；请主动重新读取。');
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function integer(value: unknown, min = 0): value is number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= min; }
function text(value: unknown): value is string { return typeof value === 'string'; }
function scope(value: unknown): boolean { return value === null || object(value) && Object.values(value).every(text); }
export function researchRequest(project: string, draft: ResearchDraft): Record<string, string> {
  const request: Record<string, string> = { project };
  for (const [key, input] of [['occurred_until', draft.occurredUntil], ['known_until', draft.knownUntil]]) {
    if (!input.trim()) continue;
    const value = input.trim();
    if (exactInstant(value) === null) throw new Error('双时间截止须为有效、带时区的 ISO 时间，例如 2026-10-10T12:00:00.000001+08:00。');
    request[key] = value;
  }
  return request;
}
export function researchQuery(reading: ResearchReading): Record<string, string | number> {
  return { project: reading.project_id, expected_revision: reading.revision,
    occurred_until: reading.occurred_until, known_until: reading.known_until };
}
export function sameReading(value: unknown, reading: ResearchReading): boolean {
  return object(value) && value.project_id === reading.project_id && value.revision === reading.revision
    && value.occurred_until === reading.occurred_until && value.known_until === reading.known_until;
}
function metadata(value: Record<string, unknown>, project: string): ResearchReading {
  if (value.project_id !== project || !integer(value.revision) || exactInstant(value.occurred_until) === null
    || exactInstant(value.known_until) === null) throw broken();
  return { project_id: project, revision: value.revision, occurred_until: value.occurred_until as string, known_until: value.known_until as string };
}
export function parseResearchRecord(value: unknown): Claim {
  if (!object(value) || !integer(value.claim_id, 1) || !text(value.claim_type) || !value.claim_type || !object(value.payload)
    || value.payload.claim_type !== undefined && value.payload.claim_type !== value.claim_type || !(value.entity_id === null || text(value.entity_id) && value.entity_id.length > 0)
    || !scope(value.scope) || !text(value.basis) || !text(value.actor)
    || !text(value.claim_state) || !['candidate', 'confirmed', 'dismissed'].includes(value.claim_state)
    || !text(value.effective_state) || !['candidate', 'confirmed', 'dismissed'].includes(value.effective_state)
    || !(value.occurred_at === null || text(value.occurred_at)) || !text(value.recorded_at)
    || !(value.replaces_claim === null || integer(value.replaces_claim, 1))
    || !Array.isArray(value.replacement_ids) || value.replacement_ids.some(id => !integer(id, 1))
    || !Array.isArray(value.evidence) || !Array.isArray(value.groups)
    || ![null, 'human', 'rule'].includes(value.confirmation_source as string | null)) throw broken();
  if (value.kind !== undefined && !text(value.kind)) throw broken();
  if (value.replaced !== undefined && (typeof value.replaced !== 'boolean' || value.replaced !== (value.replacement_ids.length > 0))) throw broken();
  for (const key of ['occurred_at_utc', 'recorded_at_utc']) {
    if (Object.hasOwn(value, key) && value[key] !== null && exactInstant(value[key]) === null) throw broken();
  }
  for (const key of ['kind', 'label', 'content', 'target', 'source', 'relation', 'action', 'reason', 'state', 'semantics', 'selected']) {
    if (value.payload[key] !== undefined && value.payload[key] !== null && !text(value.payload[key])) throw broken();
  }
  if (value.payload.inputs !== undefined && (!Array.isArray(value.payload.inputs)
    || value.payload.inputs.some(input => !object(input) || !text(input.port) || !text(input.ref)))) throw broken();
  const ids = new Set<number>();
  for (const span of value.evidence) {
    if (!object(span) || !integer(span.span_id, 1) || ids.has(span.span_id) || !integer(span.event_id, 1)
      || !integer(span.byte_start) || !integer(span.byte_end) || span.byte_end < span.byte_start
      || !text(span.quote_sha256) || span.quote_sha256.length !== 64 || !/^[a-f0-9]{64}$/.test(span.quote_sha256) || !text(span.role)
      || !integer(span.session_pk, 1) || !integer(span.seq) || !text(span.path)
      || !integer(span.source_byte_start) || !integer(span.source_byte_end) || span.source_byte_end < span.source_byte_start
      || !(span.occurred_at === null || text(span.occurred_at)) || !text(span.recorded_at)) throw broken();
    ids.add(span.span_id);
  }
  if (value.groups.some(group => !object(group) || !integer(group.session_pk, 1)
    || !(group.segment_id === null || text(group.segment_id)))) throw broken();
  if (value.review !== null && (!object(value.review) || !integer(value.review.action_id, 1)
    || !text(value.review.action) || !text(value.review.actor))) throw broken();
  if (value.review_history !== undefined && (!Array.isArray(value.review_history) || value.review_history.some(review =>
    !object(review) || !integer(review.action_id, 1) || !text(review.action) || !text(review.actor)))) throw broken();
  return value as unknown as Claim;
}
export function parseResearchPage(value: unknown, project: string, offset: number, fixed?: ResearchPage, request?: Record<string, string>): ResearchPage {
  if (!object(value)) throw broken();
  const reading = metadata(value, project);
  if (value.layer !== 'L2' || value.projection !== false || value.semantic_graph_complete !== true || value.collection !== 'claims'
    || value.scope !== null || value.scope_filter !== false || value.offset !== offset
    || !integer(value.total) || value.claims_total !== value.total || !Array.isArray(value.items)
    || value.items.length > 100 || offset + value.items.length > value.total
    || !(value.next_offset === null ? offset + value.items.length === value.total
      : integer(value.next_offset) && value.next_offset === offset + value.items.length && value.next_offset < value.total && value.items.length > 0)) throw broken();
  if (fixed && (!sameReading(value, fixed) || value.total !== fixed.total)) throw broken();
  for (const key of ['occurred_until', 'known_until'] as const) {
    if (request?.[key] && exactInstant(request[key]) !== exactInstant(reading[key])) throw broken();
  }
  return { ...reading, scope: null, scope_filter: false, total: value.total, claims_total: value.total,
    offset, next_offset: value.next_offset as number | null, items: value.items.map(parseResearchRecord) };
}
export function appendResearchPage(records: Claim[], page: ResearchPage): Claim[] {
  if (records.length !== page.offset) throw broken();
  const seen = new Set(records.map(item => item.claim_id));
  for (const item of page.items) { if (seen.has(item.claim_id)) throw broken(); seen.add(item.claim_id); }
  return [...records, ...page.items];
}
export function researchData(page: ResearchPage, claims: Claim[], intent: string): ResearchGraphData {
  return { project_id: page.project_id, revision: page.revision, occurred_until: page.occurred_until, known_until: page.known_until,
    scope: null, scope_filter: false, total: page.total, claims, intent, partial: claims.length !== page.total };
}
export function parseResearchDetail(value: unknown, reading: ResearchReading, id: number): { revision: number; claim: Claim } {
  if (!object(value) || !sameReading(value, reading) || value.history_context !== true) throw broken();
  const claim = parseResearchRecord(value.claim); if (claim.claim_id !== id) throw broken();
  return { revision: reading.revision, claim };
}
export function validateResearchEvidence(value: EvidenceData, target: EvidenceTarget): void {
  if (!target.reading) return;
  if (!sameReading(value, target.reading) || value.history_context !== true || value.derived_context_loaded !== false
    || value.event?.event_id !== target.event_id || !Array.isArray(value.before) || !Array.isArray(value.after)) throw broken();
  for (const window of [value.event, ...value.before, ...value.after]) {
    if (!window || !integer(window.event_id, 1) || !text(window.before) || !text(window.quote) || !text(window.after)
      || !integer(window.total_bytes) || !integer(window.window_start) || !integer(window.window_end)
      || window.window_end < window.window_start || window.window_end > window.total_bytes) throw broken();
    const known = exactInstant(window.recorded_at);
    const happened = exactInstant(window.occurred_at);
    if (known === null || known > exactInstant(target.reading.known_until)!
      || happened !== null && happened > exactInstant(target.reading.occurred_until)!) throw broken();
  }
}
