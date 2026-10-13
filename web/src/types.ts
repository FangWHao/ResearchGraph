export type Kind = 'question' | 'attempt' | 'finding' | 'decision' | 'output';
export const KINDS: Kind[] = ['question', 'attempt', 'finding', 'decision', 'output'];
export const KIND_LABEL: Record<Kind, string> = {
  question: '问题',
  attempt: '尝试',
  finding: '发现',
  decision: '决定',
  output: '产出',
};
export type Tool = 'claude' | 'codex';
export const TOOL_LABEL: Record<Tool, string> = { claude: 'Claude Code', codex: 'Codex' };

export interface DetectedDir {
  path: string;
  tool: Tool;
  files: number;
}

export interface ModelSettings {
  base_url: string;
  model: string;
  has_key: boolean;
}

export interface ProjectSummary {
  id: number;
  name: string;
  workspace: string | null;
  follow: number;
  model_allowed: number;
  created_at: string;
  organized_at: string | null;
  sessions?: number;
  events?: number;
}

export interface AppState {
  sources: string[];
  detected: DetectedDir[];
  model: ModelSettings;
  projects: ProjectSummary[];
}

export interface SessionInfo {
  path: string;
  tool: Tool;
  native_id: string | null;
  cwd: string | null;
  title: string | null;
  started_at: string | null;
  ended_at: string | null;
  size: number;
  mtime: number;
}

export interface Workspace {
  cwd: string;
  sessions: number;
  claude: number;
  codex: number;
  last: string | null;
}

export interface Session {
  id: number;
  tool: Tool;
  path: string;
  title: string | null;
  cwd: string | null;
  started_at: string | null;
  ended_at: string | null;
  message_count: number;
  extracted_upto: number;
  missing: number;
  fork_of: number | null;
  copied: number;
  fork_event: number | null;
}

export interface Thread {
  id: number;
  project_id: number;
  title: string;
  summary: string;
  edited: number;
}

export interface Branch {
  id: number;
  thread_id: number;
  title: string;
  position: number;
  from_event: number | null;
  into_event: number | null;
  edited: number;
}

export interface TimelineEvent {
  id: number;
  session_id: number;
  branch_id: number | null;
  kind: Kind;
  title: string;
  summary: string;
  ts: string | null;
  anchor: number;
  refs: number[];
  edited: number;
}

export type Phase = 'idle' | 'sync' | 'consent' | 'extract' | 'threads' | 'done' | 'error';

export interface Estimate {
  sessions: number;
  chars: number;
  calls: number;
}

export interface Status {
  phase: Phase;
  done: number;
  total: number;
  running: boolean;
  errors: string[];
  estimate: Estimate | null;
  finished_at: string | null;
}

export interface ProjectData {
  project: ProjectSummary;
  sessions: Session[];
  threads: Thread[];
  branches: Branch[];
  events: TimelineEvent[];
  status: Status;
}

export interface Message {
  idx: number;
  role: 'user' | 'assistant' | 'tool';
  ts: string | null;
  text: string;
  ref: boolean;
}
