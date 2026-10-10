import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { parsePersonalRecord, personalIdentity, personalSaveBody, samePersonalReading, viewFingerprint } from './viewState';
import type { PersonalView, PersonalViewRecord } from './types';

interface State { record: PersonalViewRecord | null; known: boolean; busy: boolean; error: string; info: string }
const empty = (): State => ({ record: null, known: false, busy: false, error: '', info: '' });
export function useViewState({ project, user, intent, authorized, epoch, onError }: { project: string; user: string; intent: string; authorized: boolean; epoch: number; onError: (error: unknown) => void }) {
  const identity = personalIdentity(project, user); const [states, setStates] = useState<Record<string, State>>({}); const values = useRef(states);
  const key = JSON.stringify([identity, intent, authorized, epoch]); const current = useRef({ key, identity }); current.current = { key, identity };
  const callbacks = useRef(onError); callbacks.current = onError;
  const pending = useRef<{ key: string; identity: string; controller: AbortController } | null>(null);
  function update(id: string, patch: Partial<State>) { values.current = { ...values.current, [id]: { ...(values.current[id] ?? empty()), ...patch } }; setStates(values.current); }
  function valid(request: NonNullable<typeof pending.current>) { return pending.current === request && !request.controller.signal.aborted && current.current.key === request.key && current.current.identity === request.identity; }
  function cancel(message = '') { const request = pending.current; pending.current = null; request?.controller.abort(); if (request) update(request.identity, { busy: false, info: message }); }
  async function read(): Promise<PersonalViewRecord | null> {
    if (!project || !user.trim() || !authorized || pending.current) return null;
    const request = { key, identity, controller: new AbortController() }; pending.current = request;
    update(identity, { busy: true, error: '', info: '' });
    try {
      const response = await api<unknown>(`/view-state?${query({ project, user: user.trim() })}`, undefined, request.controller.signal);
      if (!valid(request)) return null;
      const record = parsePersonalRecord(response, project, user);
      update(identity, { known: true, record, info: record.unavailable_reason ?? (record.view_id === null ? '此标识尚未保存个人视图。' : '已读取保存记录；只有重新核对完整研究图后才恢复。') });
      return record;
    } catch (error) { if (valid(request)) { update(identity, { error: (error as Error).message }); if (error instanceof ApiError && error.status === 401) callbacks.current(error); } return null; }
    finally { if (pending.current === request) { pending.current = null; update(identity, { busy: false }); } }
  }
  async function save(view: PersonalView): Promise<PersonalViewRecord | null> {
    const state = values.current[identity]; if (!authorized || pending.current || !state?.known) return null;
    const request = { key, identity, controller: new AbortController() }; pending.current = request;
    update(identity, { busy: true, error: '', info: '' });
    try {
      const body = personalSaveBody(project, user, state.record?.view_id ?? null, view);
      const response = await api<unknown>('/view-state', body, request.controller.signal);
      if (!valid(request)) return null;
      const record = parsePersonalRecord(response, project, user);
      if (record.view === null || record.view_id === null || record.view_id === body.expected_view_id || record.current_revision !== view.reading.revision || !samePersonalReading(record.view.reading, view.reading) || viewFingerprint({ ...record.view, reading: view.reading }) !== viewFingerprint(view)) throw new Error('保存回执与本次个人视图不符，未报保存成功。');
      update(identity, { record, known: true, info: '个人视图已保存；研究记录、原文与正式状态未改变。' }); return record;
    } catch (error) { if (valid(request)) { update(identity, { error: (error as Error).message + (error instanceof ApiError && error.status === 409 ? ' 请主动读取保存记录及研究图后再决定，不自动覆盖。' : '') }); if (error instanceof ApiError && error.status === 401) callbacks.current(error); } return null; }
    finally { if (pending.current === request) { pending.current = null; update(identity, { busy: false }); } }
  }
  useEffect(() => {
    cancel(); update(identity, { error: '', info: '' });
    if (project && user.trim() && authorized) void read();
    return () => { const request = pending.current; request?.controller.abort(); pending.current = null; if (request) update(request.identity, { busy: false }); };
  }, [identity, authorized, epoch]);
  useEffect(() => { if (pending.current && pending.current.key !== key) cancel(); }, [key]);
  return { ...(states[identity] ?? empty()), identity, read, save, cancel: () => { const waiting = pending.current !== null; cancel(); update(identity, { info: waiting ? '已停止页面等待；服务端可能已经保存，请主动读取回执。' : '个人布局或阅读条件已改变；需主动保存。' }); } };
}
