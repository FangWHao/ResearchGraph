// 事件只记三类：尝试（带成败）、决定、结果。question 是旧版本提取的，只用来显示。
export type Kind = 'attempt' | 'decision' | 'result' | 'question';
export const KINDS: Kind[] = ['attempt', 'decision', 'result'];
export const KIND_LABEL: Record<Kind, string> = {
  attempt: '尝试',
  decision: '决定',
  result: '结果',
  question: '问题',
};
export type Outcome = 'success' | 'failed' | 'open';
export const OUTCOMES: Outcome[] = ['success', 'failed', 'open'];
export const OUTCOME_LABEL: Record<Outcome, string> = { success: '成功', failed: '失败', open: '未定' };
// 1 细节，2 关键，3 里程碑
export type Importance = 1 | 2 | 3;
export const IMPORTANCE_LABEL: Record<Importance, string> = { 1: '细节', 2: '关键', 3: '里程碑' };
// “尝试 · 失败 · 里程碑”这样的完整说法，用在提示框和读屏
export function eventLabel(e: { kind: Kind; outcome: Outcome | null; importance: Importance }): string {
  return [KIND_LABEL[e.kind], e.outcome ? OUTCOME_LABEL[e.outcome] : '', e.importance === 3 ? '里程碑' : '']
    .filter(Boolean)
    .join(' · ');
}
export type ThreadStatus = 'active' | 'done' | 'dropped';
export const STATUS_LABEL: Record<ThreadStatus, string> = { active: '进行中', done: '已完成', dropped: '已放弃' };
export type Tool = 'claude' | 'codex';
export const TOOL_LABEL: Record<Tool, string> = { claude: 'Claude Code', codex: 'Codex' };

export interface DetectedDir {
  path: string;
  tool: Tool;
  files: number;
}

// 三类任务分开选模型：提取调用最多、看一段对话；归类和复审调用少、要看整个项目
export type Stage = 'extract' | 'threads' | 'review';
export const STAGES: Stage[] = ['extract', 'threads', 'review'];
export type Thinking = 'off' | 'low' | 'high' | 'max';
export const THINKING_LABEL: Record<Thinking, string> = { off: '不思考', low: '低', high: '高', max: '最高' };
export interface StageModel {
  model: string;
  thinking: Thinking;
}

export interface ModelSettings {
  base_url: string;
  model: string; // 提取用的模型，旧界面文字里用
  models: Record<Stage, StageModel>;
  has_key: boolean;
}

// 接口返回的模型
export interface ModelInfo {
  id: string;
  name: string;
  context_window: number | null;
  max_output_tokens: number | null;
  thinking: Thinking[];
  default_thinking: Thinking | null;
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
  status: ThreadStatus | null;
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
  outcome: Outcome | null;
  importance: Importance;
  title: string;
  summary: string;
  ts: string | null;
  anchor: number;
  refs: number[];
  edited: number;
}

export type Phase = 'idle' | 'sync' | 'consent' | 'confirm' | 'extract' | 'threads' | 'consolidate' | 'review' | 'done' | 'error';

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
  stages: (UsageBucket & { stage: Stage })[];
  models: (UsageBucket & { model: string })[];
}
