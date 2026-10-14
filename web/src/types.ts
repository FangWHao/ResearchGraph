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

// 自动更新：打开的项目在会话安静 quiet 分钟后整理新内容；新内容超过 limit 字先问（null 不限）；
// 进入项目时发现离开期间的新内容，on_open = ask 先问，auto 直接更新。
export interface AutoSettings {
  enabled: boolean;
  quiet: number;
  limit: number | null;
  on_open: 'ask' | 'auto';
}

export interface AppState {
  sources: string[];
  detected: DetectedDir[];
  model: ModelSettings;
  auto: AutoSettings;
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

export type Phase = 'idle' | 'sync' | 'consent' | 'confirm' | 'extract' | 'threads' | 'done' | 'error';

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
  auto: boolean;
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
  focus?: boolean; // 从搜索结果点进来时命中的那条
}

export interface HiddenEvent {
  id: number;
  kind: Kind;
  title: string;
  summary: string;
  ts: string | null;
  session_title: string | null;
}

export interface SearchHit {
  event: number;
  session_id: number;
  idx: number;
  role: Message['role'];
  ts: string | null;
  snippet: string;
}

export interface SearchResult {
  terms: string[];
  events: number[];
  messages: SearchHit[];
  more: boolean;
}

// 只看文件大小和修改时间得到的“有没有新内容”
export interface Pending {
  changed: number;
  new: number;
  chars: number;
  unplaced: number; // 已提取、还没归入研究线的事件
  quiet: number | null;
}

export interface UsageBucket {
  calls: number;
  cached: number;
  input: number;
  output: number;
  cache_read: number;
}

export interface UsageReport {
  since: string | null;
  periods: Record<'today' | 'week' | 'month' | 'all', UsageBucket>;
  days: (UsageBucket & { day: string })[];
  projects: (UsageBucket & { id: number | null; name: string | null; last: string })[];
  stages: (UsageBucket & { stage: 'extract' | 'threads' })[];
}
