import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { emptyVersionDiffDraft, parseVersionDiff, versionDiffIntent, versionDiffRequest } from './versionDiff';
import type { VersionDiffDraft, VersionDiffResult } from './versionDiff';

export function useVersionDiff({ project, revision, epoch, onError, onRefresh }: { project: string; revision: number | null; epoch: number; onError: (error: unknown) => void; onRefresh: () => void }) {
  const [draft, setDraft] = useState<VersionDiffDraft>(emptyVersionDiffDraft);
  const [result, setResult] = useState<{ data: VersionDiffResult; generation: number } | null>(null); const [error, setError] = useState('');
  const [busy, setBusy] = useState(false); const [conflict, setConflict] = useState(false);
  const current = useRef({ project, revision, epoch, draft, generation: 0 });
  if (current.current.project !== project || current.current.revision !== revision || current.current.epoch !== epoch) current.current.generation++;
  current.current = { project, revision, epoch, draft, generation: current.current.generation };
  const pending = useRef<AbortController | null>(null);
  useEffect(() => { pending.current?.abort(); setResult(null); setError(''); setBusy(false); setConflict(false); }, [project, revision, epoch]);
  useEffect(() => { setDraft(emptyVersionDiffDraft()); }, [project]);
  useEffect(() => () => pending.current?.abort(), []);
  function change(patch: Partial<VersionDiffDraft>) {
    pending.current?.abort(); current.current.generation++;
    const next = { ...current.current.draft, ...patch }; current.current.draft = next;
    setDraft(next); setResult(null); setError(''); setBusy(false);
  }
  async function compare() {
    if (busy || conflict || pending.current && !pending.current.signal.aborted) return;
    const generation = current.current.generation; const intent = versionDiffIntent(project, draft);
    const controller = new AbortController(); pending.current = controller;
    const valid = () => !controller.signal.aborted && pending.current === controller && current.current.generation === generation && versionDiffIntent(current.current.project, current.current.draft) === intent;
    try {
      const request = versionDiffRequest(project, draft, revision); setBusy(true); setError(''); setResult(null);
      const response = await api<unknown>(`/version-diff?${query({ ...request })}`, undefined, controller.signal);
      if (!valid()) return;
      setResult({ data: parseVersionDiff(response, request), generation });
    } catch (e) {
      if (!valid()) return;
      setError(e instanceof Error ? e.message : '文件差异读取失败。'); setConflict(e instanceof ApiError && e.status === 409);
      if (e instanceof ApiError && e.status === 401) onError(e);
    } finally { if (valid()) { pending.current = null; setBusy(false); } }
  }
  function refresh() { pending.current?.abort(); setResult(null); setError(''); setBusy(false); setConflict(false); onRefresh(); }
  return { draft, change, result: result?.generation === current.current.generation ? result.data : null, error, busy, conflict, compare, refresh, revision };
}
