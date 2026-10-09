export type ReviewState = 'candidate' | 'confirmed' | 'dismissed';
export type Kind = 'question' | 'approach' | 'attempt' | 'finding' | 'decision' | 'join';
export interface Span {
  span_id: number; event_id: number; byte_start: number; byte_end: number;
  quote_sha256: string; role: string; session_pk: number; seq: number;
  path: string; source_byte_start: number; source_byte_end: number;
  occurred_at: string | null; recorded_at: string;
}
export interface Review {
  action_id: number; action: string; actor: string; reason: string | null;
  new_claim_id: number | null; recorded_at: string;
}
export interface Payload {
  claim_type: string; kind?: Kind; label?: string; content?: string; temp_id?: string;
  target?: string; source?: string; relation?: string; action?: string; reason?: string;
  state?: string; semantics?: string; selected?: string | null;
  inputs?: { port: string; ref: string }[];
  [key: string]: unknown;
}
export interface Claim {
  claim_id: number; claim_type: string; entity_id: string | null;
  payload: Payload; scope: Record<string, string> | null; basis: string; actor: string;
  claim_state: ReviewState; effective_state: ReviewState;
  occurred_at: string | null; recorded_at: string;
  replaces_claim: number | null; replacement_ids: number[]; review: Review | null;
  confirmation_source: 'human' | 'rule' | null;
  evidence: Span[]; groups: { segment_id: string | null; session_pk: number }[];
  review_history?: Review[];
  replaces?: { claim_id: number; payload: Payload; scope: Record<string, string> | null };
  human_comparisons?: { claim_id: number; actor: string; scope: Record<string, string>; payload: Payload; differing_fields: string[] }[];
}
export interface Project {
  project_id: string; name: string; remote_model_allowed: number; sessions: number; claims: number;
}
export interface ClaimsPage {
  revision: number; total: number; next_offset: number | null; offset: number; claims: Claim[];
}
export interface GraphData {
  revision: number; claims: Claim[]; partial: boolean; limit: number;
  run_states: { run_id: string; state: string; exit_code: number | null }[];
  capabilities: { impact_propagation: boolean; artifact_diff: boolean };
}
export interface EventWindow {
  event_id: number; session_pk: number; seq: number; kind: string; role: string | null;
  path: string; source_byte_start: number; source_byte_end: number;
  occurred_at: string | null; recorded_at: string; exclude_reason: string | null;
  total_bytes: number; window_start: number; window_end: number; window_truncated: boolean;
  before: string; quote: string; after: string; quote_sha256: string | null;
}
export interface EvidenceData {
  event: EventWindow; before: EventWindow[]; after: EventWindow[];
  artifact_versions: { version_id: string; path: string; algo: string; digest: string; source: string }[];
  artifact_diff: { available: boolean; reason: string };
}
export interface SearchPage {
  results: { event_id: number; session_pk: number; text: string; occurred_at: string | null }[];
  total: number; next_offset: number | null; offset: number;
}
export interface HealthData {
  events: number; unknown: number; bad_lines: number; unassigned_sessions: number;
  pending: number; manual_jobs: number; revision: number; global_counts: boolean;
  compression_points: number; hook_failures: number | null; hook_failures_reason: string;
  ingest?: {
    registered_sources: number; known_source_paths: number; spool_receipts: number;
    spool_unfinished: number; spool_failed: number;
  };
  snapshots?: {
    total: number; skipped: number; async_race: number; partial: number; metadata_unknown: number;
  };
  sources: {
    file_instance_id: number; path: string; tool: string; last_read: string | null;
    committed_offset: number; cursor_lag_bytes: number | null; cleanup_risk: string | null; status: string;
  }[];
  extraction: {
    day_utc: string; alerts: { code: string; message: string; count?: number; stage?: string }[];
    coverage: {
      pending_event_stages: number; next_offset: number | null; offset: number;
      gaps: { event_id: number; session_pk: number; stage: string; segment_id: string | null }[];
    };
    daily_usage: {
      budget_tokens: number; reserved_or_settled_tokens: number; remaining_tokens: number;
      known_input_tokens: number; known_output_tokens: number; unsettled_sent_attempts: number;
    };
    stages: { stage: string; attempts: number; sent: number; mean_utilization: number | null;
      utilization_samples?: number; validation_samples?: number;
      validation_rejections: number; citation_failures: number }[];
  };
}
