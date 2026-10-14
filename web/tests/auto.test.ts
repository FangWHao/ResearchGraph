import { describe, expect, it } from 'vitest';
import { autoState, type AutoInput } from '../src/auto';
import type { Status } from '../src/types';

const status = (extra: Partial<Status> = {}): Status => ({
  phase: 'done',
  done: 0,
  total: 0,
  running: false,
  auto: false,
  errors: [],
  estimate: null,
  finished_at: null,
  ...extra,
});

const base: AutoInput = {
  settings: { enabled: true, quiet: 3, limit: 200_000, on_open: 'ask' },
  allowed: true,
  hasKey: true,
  paused: false,
  asking: false,
  status: status(),
  pending: { changed: 1, new: 0, chars: 0, unplaced: 0, quiet: 600 },
};

describe('自动更新', () => {
  it('会话安静够久才更新，还在写就等着', () => {
    expect(autoState(base)).toEqual({ kind: 'go' });
    expect(autoState({ ...base, pending: { changed: 1, new: 0, chars: 0, unplaced: 0, quiet: 40 } })).toEqual({
      kind: 'waiting',
      seconds: 140,
    });
    // 只剩上次没整理完的部分：文件没有在变，不用等
    expect(autoState({ ...base, pending: { changed: 0, new: 0, chars: 900, unplaced: 0, quiet: null } })).toEqual({ kind: 'go' });
    // 上次归类出错，事件还没归入研究线：也算有新内容
    expect(autoState({ ...base, pending: { changed: 0, new: 0, chars: 0, unplaced: 2490, quiet: null } })).toEqual({ kind: 'go' });
  });

  it('没同意发送、没有 Key、点过稍后、正在问、出错时都不会自动调用模型', () => {
    const blocked = [
      { allowed: false },
      { hasKey: false },
      { paused: true },
      { asking: true },
      { status: status({ running: true }) },
      { status: status({ phase: 'error' }) },
      { status: status({ phase: 'confirm' }) },
      { status: status({ phase: 'consent' }) },
    ];
    for (const extra of blocked) expect(autoState({ ...base, ...extra }).kind).toBe('blocked');
    expect(autoState({ ...base, settings: { ...base.settings, enabled: false } })).toEqual({ kind: 'off' });
    expect(autoState({ ...base, pending: { changed: 0, new: 0, chars: 0, unplaced: 0, quiet: null } })).toEqual({ kind: 'idle' });
  });
});
