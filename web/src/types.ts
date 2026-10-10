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
  target?: string | null; source?: string; relation?: string; action?: string; reason?: string;
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
  pending_decision?: PendingDecision | null;
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
export interface QuestionRequest {
  project_id: string; text: string; scope: Record<string, string> | null;
  actor: string; expected_revision: number; request_id: string;
}
export interface QuestionResult {
  revision: number; claim_id: number; entity_id: string; event_id: number;
  request_id: string; replayed: boolean;
}
export type DecisionAction = 'accept' | 'defer' | 'reject' | 'withdraw';
export interface PendingDecision {
  request_id: string; selector: string; action: string; why: string; scope: Record<string, string> | null;
  target_id: string | null; requires_resolution: boolean; resolved_claim_id: number | null;
}
export interface DecisionRequest {
  project_id: string; selector: string; action: DecisionAction; why: string; scope: Record<string, string> | null;
  actor: string; request_id: string; expected_revision: number;
}
export interface DecisionResult {
  revision: number; request_id: string; claim_id: number; event_id: number; target_id: string | null;
  effective_state: ReviewState; replayed: boolean; resolved_claim_id: number | null;
}
export interface DecisionTarget {
  entity_id: string; kind: Kind; claim_id: number; label: string; label_truncated: boolean;
  label_total_bytes: number; scope: Record<string, string> | null;
}
export interface DecisionTargetsPage {
  revision: number; total: number; next_offset: number | null; offset: number; targets: DecisionTarget[];
}
export interface ResolveDecisionRequest { target_id: string; actor: string; request_id: string; expected_revision: number }
export interface ResolveDecisionResult {
  revision: number; request_id: string; claim_id: number; event_id: number;
  original_claim_id: number; original_event_id: number; replayed: boolean;
}
export interface EventWindow {
  event_id: number; session_pk: number; seq: number; kind: string; role: string | null;
  path: string; source_byte_start: number; source_byte_end: number;
  occurred_at: string | null; recorded_at: string; exclude_reason: string | null;
  total_bytes: number; window_start: number; window_end: number; window_truncated: boolean;
  before: string; quote: string; after: string; quote_sha256: string | null;
}
export type EvidenceTarget = { event_id: number; byte_start?: number; byte_end?: number; quote_sha256?: string };
export interface ArtifactVersion {
  version_id: string; path: string; algo: string; digest: string; source: string;
  phase?: string | null; basis?: string | null; claim_state?: ReviewState | null;
  representation?: string | null; observed_at?: string | null; size?: number | null;
  content_sha256?: string | null; project_id?: string; root_id?: string | null;
  evidence_event_id?: number | null;
}
export interface ArtifactObservation {
  observation_id: string; version_id: string; job_id: number;
  snapshot_id: number | null; discovery_snapshot_id: number; mode: string;
  signature: unknown; hash_started_at: string | null; hash_finished_at: string | null;
  cache_reused: boolean; cached_from: string | null; details: Record<string, unknown>;
  recorded_at: string;
}
export interface FileVersionRecord extends Partial<ArtifactVersion> {
  observations?: Partial<ArtifactObservation>[]; observations_total?: number;
  observations_partial?: boolean;
}
export interface VersionsPage {
  revision?: number; versions?: FileVersionRecord[]; total?: number;
  limit?: number; offset?: number; partial?: boolean;
}
export interface ArtifactHealth {
  versions?: number; archived?: number; current_hashed?: number; cache_reused?: number;
  jobs?: Partial<Record<'queued' | 'running' | 'paused' | 'failed' | 'done', number>>;
  discovery?: Partial<Record<'pending' | 'unknown' | 'partial' | 'done', number>>;
}
export type RunState = 'requested' | 'started' | 'exited' | 'unknown';
export interface RunObservation {
  event_id: number; state: RunState; exit_code: number | null;
  executor_session_id: number | null; reason: string | null; details: Record<string, unknown>;
  occurred_at: string | null; recorded_at: string;
}
export interface L1Run {
  run_id: string; project_id: string; session_pk: number | null; call_id: string | null;
  command: string | null; command_truncated: boolean; command_total_bytes: number;
  cwd: string | null; snapshot_id: number | null; root_id: string | null;
  state: RunState; exit_code: number | null; gap: string | null;
  request_event_id: number | null; requested_at: string | null;
  started_at: string | null; ended_at: string | null;
  observations: RunObservation[]; observations_partial: boolean;
}
export interface EditDiff {
  available: boolean; format?: 'reported_versions' | 'patch_only'; text?: string;
  complete_versions?: boolean; gap: string | null; reason: string;
}
export interface L1Edit {
  edit_id: string; project_id: string; session_pk: number; call_id: string | null;
  request_event_id: number; result_event_id: number; path: string | null; root_id: string | null;
  operation: string; patch_sha256: string | null; before_version: string | null;
  after_version: string | null; gap: string | null; association_gap?: string | null;
  user_modified: number | null; occurred_at: string | null; recorded_at: string; diff: EditDiff;
}
export interface L1Evidence {
  runs: L1Run[]; runs_partial: boolean; edits: L1Edit[]; edits_partial: boolean;
  derivation?: { state: 'queued' | 'waiting' | 'done' | 'failed'; error: string | null; updated_at: string } | null;
}
export interface EvidenceData {
  event: EventWindow; before: EventWindow[]; after: EventWindow[];
  artifact_versions: ArtifactVersion[];
  artifact_diff: { available: boolean; reason: string };
  artifact_versions_partial?: boolean;
  l1?: L1Evidence | null;
}
export interface SearchPage {
  results: { event_id: number; session_pk: number; text: string; occurred_at: string | null }[];
  total: number; next_offset: number | null; offset: number;
}
export interface HealthData {
  events: number; unknown: number; bad_lines: number; unassigned_sessions: number;
  pending: number; manual_jobs: number; revision: number; global_counts: boolean;
  compression_points: number; hook_failures: number | null; hook_failures_reason: string;
  extraction_queue?: ExtractionQueue | null;
  pipeline_queue?: PipelineQueue | null;
  artifacts?: ArtifactHealth | null;
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

export type ExtractionQueueState = 'queued' | 'running' | 'done' | 'partial' | 'paused' | 'blocked' | 'cancelled';
export interface ExtractionQueueTask {
  queue_id: number; session_pk: number; project_id: string; model: string; max_event_id: number;
  state: string; attempts: number; error: string | null; defer_reason: string | null;
  next_attempt_at: string | null; created_at: string; updated_at: string;
}
export interface ExtractionQueue {
  scope?: 'project' | 'all_projects' | null; total?: number;
  counts?: Partial<Record<ExtractionQueueState, number>>;
  tasks?: Partial<ExtractionQueueTask>[]; limit?: number; offset?: number; next_offset?: number | null;
}

export interface PipelineQueueTask {
  queue_id: number; project_id: string; session_pk: number | null;
  stage: string; target_key: string; state: string; attempts: number;
  error: string | null; defer_reason: string | null; next_attempt_at: string | null;
  created_at: string; updated_at: string; result: string | null;
}
export interface PipelineQueue {
  scope?: 'project' | 'all_projects' | null; total?: number;
  counts?: Partial<Record<ExtractionQueueState, number>>;
  tasks?: Partial<PipelineQueueTask>[]; limit?: number; offset?: number; next_offset?: number | null;
}
