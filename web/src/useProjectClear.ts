import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { clearIntent, parseClearPreview, parseClearStatus, sameClearPreview } from './projectClear';
import type { ClearIntent, ClearPreview, ClearRecord, ClearStatus } from './projectClear';
import type { Project } from './types';

export function useProjectClear({ project, authorized, active, epoch, probe, onError, onComplete }: {
  project: Project | undefined; authorized: boolean; active: boolean; epoch: number; probe: boolean;
  onError: (error: unknown) => void; onComplete: (record: ClearRecord) => void;
}) {
  const [preview, setPreview] = useState<ClearPreview | null>(null); const [confirmation, setConfirmation] = useState('');
  const [status, setStatus] = useState<ClearStatus | null>(null); const [receipt, setReceipt] = useState<ClearRecord | null>(null);
  const [error, setError] = useState(''); const [conflict, setConflict] = useState(false);
  const [loading, setLoading] = useState(false); const [busy, setBusy] = useState(false); const [checking, setChecking] = useState(false);
  const restoredReceipt = useRef(sessionStorage.getItem('rg_clear_receipt'));
  const context = useRef({ project: project?.project_id, authorized, active, epoch, generation: 0, authorization: 0 });
  if (context.current.authorized !== authorized) context.current.authorization++;
  if (context.current.project !== project?.project_id || context.current.authorized !== authorized || context.current.active !== active || context.current.epoch !== epoch) context.current.generation++;
  context.current = { project: project?.project_id, authorized, active, epoch, generation: context.current.generation, authorization: context.current.authorization };
  const callbacks = useRef({ onError, onComplete }); callbacks.current = { onError, onComplete };
  const intent = useRef<ClearIntent | null>(null); const operation = useRef(false); const previewRequest = useRef<AbortController | null>(null);
  const statusSequence = useRef(0); const notified = useRef(new Set<string>(restoredReceipt.current ? [restoredReceipt.current] : [])); const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; previewRequest.current?.abort(); statusSequence.current++; }; }, []);
  function authenticated(generation: number) { return alive.current && context.current.authorized && context.current.authorization === generation; }
  function accept(record: ClearRecord) {
    setStatus(record);
    if (record.state === 'complete') {
      setReceipt(record);
      if (!notified.current.has(record.request_id)) { notified.current.add(record.request_id); callbacks.current.onComplete(record); }
    }
  }
  async function checkStatus(requestId?: string) {
    if (!context.current.authorized) return;
    const sequence = ++statusSequence.current; const authorization = context.current.authorization;
    setChecking(true);
    try {
      const result = parseClearStatus(await api<unknown>(`/project-clear/status?${query({ request_id: requestId })}`));
      if (!authenticated(authorization) || sequence !== statusSequence.current) return;
      if (requestId && result.state !== 'idle' && result.request_id !== requestId) throw new Error('状态回执的请求编号不一致，未认定完成。');
      if (result.state === 'idle') setStatus(result); else accept(result);
      if (requestId === restoredReceipt.current && result.state === 'complete') { sessionStorage.removeItem('rg_clear_receipt'); restoredReceipt.current = null; }
    } catch (e) {
      if (!authenticated(authorization) || sequence !== statusSequence.current) return;
      setError(e instanceof Error ? e.message : '读取清除状态失败。');
      if (e instanceof ApiError && e.status === 401) callbacks.current.onError(e);
    } finally { if (alive.current && sequence === statusSequence.current) setChecking(false); }
  }
  useEffect(() => {
    previewRequest.current?.abort(); setPreview(null); setLoading(false); setError(''); setConflict(false);
  }, [project?.project_id, authorized, epoch, active]);
  useEffect(() => { setConfirmation(''); }, [project?.project_id]);
  // 此接口独立于普通 Store；即使项目列表返回 409，仍能查挂起状态。
  useEffect(() => { if (authorized) void checkStatus(restoredReceipt.current ?? undefined); }, [authorized, epoch, probe]);
  async function readPreview() {
    const id = project?.project_id;
    if (!id || !authorized || operation.current || status?.state === 'pending') return;
    previewRequest.current?.abort(); const controller = new AbortController(); previewRequest.current = controller;
    const generation = context.current.generation; setLoading(true); setPreview(null); setError(''); setConflict(false);
    try {
      const result = parseClearPreview(await api<unknown>('/project-clear/preview', { project_id: id }, controller.signal), id);
      if (controller.signal.aborted || context.current.generation !== generation) return;
      if (!preview || !sameClearPreview(preview, result)) intent.current = null;
      setPreview(result);
    } catch (e) {
      if (controller.signal.aborted || context.current.generation !== generation) return;
      setError(e instanceof Error ? e.message : '读取清除预览失败。'); setConflict(e instanceof ApiError && e.status === 409);
      if (e instanceof ApiError && e.status === 401) callbacks.current.onError(e);
      if (e instanceof ApiError && e.status === 409) void checkStatus();
    } finally { if (!controller.signal.aborted && context.current.generation === generation) setLoading(false); }
  }
  async function submit(resume: boolean) {
    if (operation.current || !authorized) return;
    let body: ClearIntent | { request_id: string }; let expected: ClearIntent | undefined;
    try {
      if (resume) {
        if (status?.state !== 'pending') return;
        expected = { project_id: status.project_id, preview_sha256: status.preview_sha256, request_id: status.request_id };
        body = { request_id: status.request_id };
      } else {
        if (!project || !preview || conflict || loading || status?.state === 'pending') return;
        const requestId = intent.current && intent.current.project_id === project.project_id && intent.current.preview_sha256 === preview.preview_sha256 ? intent.current.request_id : crypto.randomUUID();
        expected = clearIntent(preview, project.project_id, project.name, confirmation, requestId); intent.current = expected; body = expected;
      }
    } catch (e) { setError(e instanceof Error ? e.message : '清除确认无效。'); return; }
    const authorization = context.current.authorization; const generation = context.current.generation;
    operation.current = true; statusSequence.current++; setChecking(false); setBusy(true); setError('');
    try {
      const result = parseClearStatus(await api<unknown>(resume ? '/project-clear/resume' : '/project-clear/execute', body), expected);
      if (!authenticated(authorization)) return;
      if (result.state !== 'complete') throw new Error('清除尚未完成，请检查状态并恢复。');
      statusSequence.current++; setChecking(false);
      accept(result);
      if (context.current.generation === generation) { setPreview(null); setConfirmation(''); setConflict(false); }
    } catch (e) {
      if (!authenticated(authorization)) return;
      if (context.current.generation === generation) {
        setError(e instanceof Error ? e.message : '未收到完成回执，请检查状态。');
        setConflict(e instanceof ApiError && e.status === 409);
      }
      if (e instanceof ApiError && e.status === 401) callbacks.current.onError(e);
      else await checkStatus(expected.request_id);
    } finally { operation.current = false; if (alive.current) setBusy(false); }
  }
  return { preview: preview?.project_id === project?.project_id ? preview : null, confirmation, setConfirmation,
    status, receipt, error, conflict, loading, busy, checking, readPreview, checkStatus: () => checkStatus(intent.current?.request_id), execute: () => submit(false), resume: () => submit(true) };
}
