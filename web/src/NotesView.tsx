import { useEffect, useState } from 'react';
import { api, query } from './api';
import { Badge, DateText, Empty, EvidenceLink, Loading, Scope, Source } from './components';
import { isNoteClaim } from './manualNote';
import type { ClaimsPage, Span } from './types';
import './notes.css';

export function NotesView({ project, epoch, onClaim, onEvidence, onError, onCreate }: {
  project: string; epoch: number; onClaim: (id: number) => void; onEvidence: (span: Span) => void;
  onError: (error: unknown) => void; onCreate: () => void;
}) {
  const [offset, setOffset] = useState(0), [data, setData] = useState<ClaimsPage | null>(null), [failure, setFailure] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setData(null); setFailure('');
    api<ClaimsPage>(`/claims?${query({ project, kind: 'note', state: 'all', limit: 50, offset })}`, undefined, controller.signal)
      .then(result => { if (controller.signal.aborted) return;
        if (result.offset !== offset || !Array.isArray(result.claims) || result.claims.some(item => !isNoteClaim(item))) throw new Error('笔记分页返回了不一致的记录，请主动刷新。');
        setData(result);
      }).catch(error => { if (!controller.signal.aborted) { setFailure(error.message); onError(error); } });
    return () => controller.abort();
  }, [project, offset, epoch, onError]);
  return <div className="notes-view">
    <div className="notes-actions"><p className="muted small">保留想法和补充说明。笔记的审核不代表方案采用或证据成立。</p><button className="button primary" onClick={onCreate}>新增研究笔记</button></div>
    {failure ? <Empty title="笔记暂不可读">{failure}</Empty> : !data ? <Loading /> : !data.claims.length ? <Empty title={offset ? '此页没有笔记记录' : '还没有研究笔记'}>可以直接记录笔记；Agent 提议的笔记会作为候选保留。</Empty> : <>
      <p className="muted small">{data.total} 条笔记记录（含修改历史） · 当前版本 {data.revision}</p>
      <div className="notes-list">{data.claims.map(claim => <article key={claim.claim_id} data-note-id={claim.claim_id}>
        <div className="card-top"><button className="text-button" onClick={() => onClaim(claim.claim_id)}>笔记 #{claim.claim_id} · 查看记录</button><Badge state={claim.effective_state} /></div>
        <p className="note-content">{claim.payload.content ?? claim.payload.label}</p>
        <p className="note-about">{claim.payload.about ? <>关联对象 <span className="mono">{claim.payload.about}</span></> : '未指定关联对象'}</p>
        <Scope value={claim.scope} /><Source claim={claim} />
        <p className="muted small">记录于 <DateText value={claim.recorded_at} /></p>
        {claim.replacement_ids.length > 0 && <p className="muted small">原笔记已被修改；{claim.replacement_ids.map(id => <button className="text-button" key={id} onClick={() => onClaim(id)}>查看修改版 #{id}</button>)}</p>}
        <div className="card-evidence">{claim.evidence.map(span => <EvidenceLink key={span.span_id} span={span} onOpen={onEvidence} />)}</div>
      </article>)}</div>
    </>}
    {data && <div className="pagination"><button className="button secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button><span>笔记偏移 {offset}</span><button className="button secondary" disabled={data.next_offset == null} onClick={() => setOffset(data.next_offset!)}>下一页</button></div>}
  </div>;
}
