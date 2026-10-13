import { useEffect, useRef, useState } from 'react';
import { apiArchive, ApiError } from './api';
import { downloadArchive, emptyExportDraft, exportFilename, exportIntentKey, exportRequest } from './historyExport';
import type { ExportDraft } from './historyExport';

export function useHistoryExport({ project, active, authorized, revision, epoch, onError, onRefresh }: {
  project: string; active: boolean; authorized: boolean; revision: number | null; epoch: number;
  onError: (error: unknown) => void; onRefresh: () => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, ExportDraft>>({});
  const draft = drafts[project] ?? emptyExportDraft();
  const [busy, setBusy] = useState(false); const [failure, setFailure] = useState(''); const [status, setStatus] = useState(''); const [needsRefresh, setNeedsRefresh] = useState(false);
  const pending = useRef<AbortController | null>(null); const releaseDownload = useRef<(() => void) | null>(null);
  const current = useRef({ project, active, authorized, key: exportIntentKey(project, draft) });
  current.current = { project, active, authorized, key: exportIntentKey(project, draft) };
  function cancel() { pending.current?.abort(); pending.current = null; setBusy(false); }
  useEffect(() => {
    pending.current?.abort(); pending.current = null; setBusy(false); setFailure(''); setStatus(''); setNeedsRefresh(false);
    return () => { pending.current?.abort(); pending.current = null; releaseDownload.current?.(); releaseDownload.current = null; };
  }, [project, active, authorized, epoch]);
  function change(next: ExportDraft) {
    cancel(); current.current.key = exportIntentKey(project, next);
    setDrafts(previous => ({ ...previous, [project]: next })); setFailure(''); setStatus('');
  }
  async function download() {
    if (pending.current || needsRefresh || !active || !authorized) return;
    let controller: AbortController | null = null;
    const key = exportIntentKey(project, draft);
    const valid = () => controller != null && !controller.signal.aborted && pending.current === controller && current.current.authorized && current.current.active && current.current.project === project && current.current.key === key;
    try {
      const request = exportRequest(project, draft, revision);
      controller = new AbortController(); pending.current = controller; setBusy(true); setFailure(''); setStatus('');
      const blob = await apiArchive(request, controller.signal);
      if (!valid()) return;
      const filename = exportFilename(project, request.expected_revision);
      releaseDownload.current?.(); releaseDownload.current = downloadArchive(blob, filename);
      setStatus(`历史阅读包已交给浏览器下载：${filename}。保存位置由浏览器决定。`);
    } catch (error) {
      if (controller == null || valid()) {
        setFailure(error instanceof Error ? error.message : '导出失败，条件已保留。');
        if (error instanceof ApiError && error.status === 409) setNeedsRefresh(true);
        if (error instanceof ApiError && error.status === 401) onError(error);
      }
    } finally { if (controller != null && pending.current === controller) { pending.current = null; setBusy(false); } }
  }
  return { draft, change, busy, failure, status, needsRefresh, revision, download,
    invalidatePolicy: (changedProject: string) => { if (current.current.project === changedProject) { cancel(); releaseDownload.current?.(); releaseDownload.current = null; setNeedsRefresh(true); setStatus('项目遮盖规则已变化，请等待最新版本读取后主动下载。'); } },
    cancel: () => { cancel(); setStatus('已停止本页等待。服务端生成可能仍完成，迟到响应不会自动下载。'); },
    refresh: onRefresh };
}
