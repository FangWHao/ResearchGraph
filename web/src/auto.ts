import type { AutoSettings, Pending, Status } from './types';

// 自动更新的判断放在这里，界面只负责按结果显示和调用。
export const POLL_MS = 20_000;

export function hasNew(pending: Pending | null | undefined): boolean {
  return !!pending && (pending.changed > 0 || pending.new > 0 || pending.chars > 0 || pending.unplaced > 0);
}

export interface AutoInput {
  settings: AutoSettings;
  allowed: boolean; // 项目已经同意过发送
  hasKey: boolean;
  paused: boolean; // 这次打开页面时点过“稍后”
  asking: boolean; // 屏幕上正在问用户
  status: Status | null;
  pending: Pending | null;
}

export type AutoState =
  | { kind: 'idle' } // 没有新内容
  | { kind: 'off' } // 设置里关掉了
  | { kind: 'blocked'; why: 'consent' | 'key' | 'paused' | 'asking' | 'running' | 'hold' }
  | { kind: 'waiting'; seconds: number } // 会话还在写，等它安静下来
  | { kind: 'go' };

// 这几种状态各有提示条，等用户处理，自动更新不插手
const HOLD: Status['phase'][] = ['consent', 'confirm', 'error'];

export function autoState(input: AutoInput): AutoState {
  const { settings, pending, status } = input;
  if (!pending || !hasNew(pending)) return { kind: 'idle' };
  if (!settings.enabled) return { kind: 'off' };
  if (!input.allowed) return { kind: 'blocked', why: 'consent' };
  if (!input.hasKey) return { kind: 'blocked', why: 'key' };
  if (input.paused) return { kind: 'blocked', why: 'paused' };
  if (input.asking) return { kind: 'blocked', why: 'asking' };
  if (!status || status.running) return { kind: 'blocked', why: 'running' };
  if (HOLD.includes(status.phase)) return { kind: 'blocked', why: 'hold' };
  const need = settings.quiet * 60;
  if (pending.quiet !== null && pending.quiet < need) return { kind: 'waiting', seconds: need - pending.quiet };
  return { kind: 'go' };
}

export function describePending(pending: Pending): string {
  const parts: string[] = [];
  if (pending.changed) parts.push(`${pending.changed} 个会话有新的对话`);
  if (pending.new) parts.push(`多了 ${pending.new} 个新会话`);
  if (!parts.length && pending.chars) parts.push('上次还有一部分没整理完');
  if (!parts.length && pending.unplaced) parts.push(`上次有 ${pending.unplaced} 个事件还没归入研究线`);
  return parts.join('，');
}
