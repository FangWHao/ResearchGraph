import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { manifestPage } from './runManifest';
import { objectFields } from './versions';
import type { L1Run } from './types';

interface Loaded { items: Record<string, unknown>[]; total: number; offset: number; next_offset: number | null }
interface Receipt { project_id: string; run_id: string; revision: number; manifests: unknown }
function receipt(value: Receipt, run: L1Run, revision: number | null, offset: number, manifest?: string, ioOffset = 0) {
  if (value.project_id !== run.project_id || value.run_id !== run.run_id || !Number.isSafeInteger(value.revision) || value.revision < 0 || revision != null && value.revision !== revision) throw new Error('清单归属或修订不一致，已保留此前列表，不合并本页。');
  const page = manifestPage(value.manifests, 20);
  if (page.issues.length || page.offset !== offset || page.total == null || manifest && (page.items.length !== 1 || page.items[0].request_id !== manifest)) throw new Error(`清单分页返回异常，已保留此前列表：${page.issues.join('；') || '页位置或清单标识不一致'}`);
  if (page.items.some(item => item.run_id !== run.run_id || item.project_id !== run.project_id || typeof item.request_id !== 'string')) throw new Error('清单条目归属缺失或不一致，已保留此前列表。');
  if (page.items.some(item => { const io = manifestPage(item.io, 100); return io.issues.length > 0 || io.offset !== ioOffset; })) throw new Error('清单 I/O 分页字段矛盾或缺失，已保留此前列表。');
  return { page: { items: page.items, total: page.total, offset, next_offset: page.nextOffset }, revision: value.revision };
}
export function useRunManifests(native: L1Run, current: number, onError?: (error: unknown) => void) {
  const [loaded, setLoaded] = useState<Loaded | null>(null); const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState(''); const [conflict, setConflict] = useState(false);
  const store = useRef<{ page: Loaded | null; revision: number | null }>({ page: null, revision: null });
  const key = `${native.project_id}:${native.run_id}:${current}`; const identity = useRef(key); identity.current = key;
  const pending = useRef<AbortController | null>(null); const source = useRef(native.manifests);
  useEffect(() => {
    pending.current?.abort(); pending.current = null; store.current = { page: null, revision: null };
    source.current = native.manifests; setLoaded(null); setBusy(null); setError(''); setConflict(false);
    return () => { pending.current?.abort(); };
  }, [key, native.manifests]);
  const page = manifestPage(loaded ?? native.manifests, loaded ? Math.max(20, loaded.items.length) : 20);
  async function read(mode: 'manifests' | 'io' | 'refresh', manifest?: string) {
    if (pending.current || native.manifests == null || conflict && mode !== 'refresh') return;
    const controller = new AbortController(); pending.current = controller;
    const original = source.current;
    const valid = () => !controller.signal.aborted && pending.current === controller && identity.current === key && source.current === original;
    setBusy(manifest ?? mode); setError('');
    try {
      if (mode === 'refresh' || store.current.revision == null || store.current.page == null) {
        const first = receipt(await api<Receipt>(`/run-evidence?${query({ project: native.project_id, run_id: native.run_id, limit: 20, offset: 0, io_offset: 0 })}`, undefined, controller.signal), native, null, 0);
        if (!valid()) return;
        store.current = { page: first.page, revision: first.revision }; setLoaded(first.page); setConflict(false);
        if (mode === 'refresh') return;
      }
      const snapshot = store.current;
      if (!snapshot.page || snapshot.revision == null) return;
      if (mode === 'manifests') {
        const offset = snapshot.page.next_offset;
        if (offset == null) return;
        const next = receipt(await api<Receipt>(`/run-evidence?${query({ project: native.project_id, run_id: native.run_id, limit: 20, offset, io_offset: 0, expected_revision: snapshot.revision })}`, undefined, controller.signal), native, snapshot.revision, offset);
        if (!valid()) return;
        if (next.page.total !== snapshot.page.total || next.page.items.some(item => snapshot.page!.items.some(old => old.request_id === item.request_id))) throw new Error('清单总数变化或条目重复，不合并此页；请刷新重新读取。');
        const combined = { ...next.page, offset: 0, items: [...snapshot.page.items, ...next.page.items] };
        store.current = { page: combined, revision: snapshot.revision }; setLoaded(combined);
      } else {
        const entry = snapshot.page.items.find(item => item.request_id === manifest);
        const io = manifestPage(entry?.io, Math.max(100, Array.isArray(objectFields(entry?.io)?.items) ? (objectFields(entry?.io)!.items as unknown[]).length : 0));
        const offset = io.nextOffset;
        if (!manifest || !entry || offset == null) return;
        const next = receipt(await api<Receipt>(`/run-evidence?${query({ project: native.project_id, run_id: native.run_id, limit: 20, offset: 0, io_offset: offset, manifest_id: manifest, expected_revision: snapshot.revision })}`, undefined, controller.signal), native, snapshot.revision, 0, manifest, offset);
        if (!valid()) return;
        const more = manifestPage(next.page.items[0].io, 100);
        if (more.issues.length || more.offset !== offset || more.total !== io.total || more.items.some(item => io.items.some(old => old.io_id === item.io_id))) throw new Error(`I/O 分页位置、总数或条目矛盾，不合并此页：${more.issues.join('；')}`);
        const all = [...io.items, ...more.items];
        const combined = { ...snapshot.page, items: snapshot.page.items.map(item => item.request_id === manifest ? { ...item, io: { items: all, total: more.total, offset: 0, next_offset: more.nextOffset, partial: more.nextOffset != null } } : item) };
        store.current = { page: combined, revision: snapshot.revision }; setLoaded(combined);
      }
    } catch (failure) {
      if (valid()) {
        setError(failure instanceof Error ? failure.message : '读取运行清单失败。');
        if (failure instanceof ApiError && failure.status === 409) setConflict(true);
        if (failure instanceof ApiError && failure.status === 401) onError?.(failure);
      }
    } finally { if (pending.current === controller) { pending.current = null; setBusy(null); } }
  }
  return { page, busy, error, conflict, revision: store.current.revision,
    moreManifests: () => read('manifests'), moreIO: (manifest: string) => read('io', manifest), refresh: () => read('refresh') };
}
