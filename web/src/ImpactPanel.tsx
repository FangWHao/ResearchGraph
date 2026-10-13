import { useEffect, useRef, useState } from 'react';
import { api, query } from './api';
import { DateText } from './components';
import { scopeText } from './model';
import { appendImpactPage, impactRequest, impactTargetKey, parseImpactPage } from './impact';
import type { ImpactCollection, ImpactItem, ImpactOptions, ImpactPage, ImpactPath, ImpactTarget } from './impact';
import type { ResearchReading } from './types';
import './impact.css';

const names: Record<string, string> = { question: '问题', approach: '方案', attempt: '尝试', finding: '发现', decision: '决定', join: '汇合', note: '笔记', artifact_version: '文件版本', native_run: '原生运行', run_manifest: '运行报告', edit_record: '编辑报告', workspace_snapshot: '快照' };
const relations: Record<string, string> = { supports: '支持', challenges: '挑战', selects: '选择', join_input: '汇合输入', consumes: '报告输入', produces: '报告输出', describes_attempt: '报告关联尝试' };
export function ImpactPanel({ target, onClaim, onNode, onError }: { target: ImpactTarget; onClaim?: (id: number, reading: ResearchReading) => void; onNode?: (id: string) => void; onError?: (error: unknown) => void }) {
  const [options, setOptions] = useState<ImpactOptions>({ candidates: false, reported: !!target.versionId });
  const [collection, setCollection] = useState<ImpactCollection>('affected');
  const [response, setResponse] = useState<{ key: string; data: ImpactPage } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const anchor = useRef<{ key: string; reading: ResearchReading } | null>(null);
  const pending = useRef<AbortController | null>(null);
  const base = impactTargetKey(target); const key = JSON.stringify([base, options, collection]);
  const current = useRef(key); current.current = key;
  const data = response?.key === key ? response.data : null; const error = failure?.key === key ? failure.message : '';
  useEffect(() => () => { pending.current?.abort(); }, [key]);
  async function load(more = false) {
    pending.current?.abort(); const controller = new AbortController(); pending.current = controller;
    const previous = more ? data : null; const offset = previous?.next_offset ?? 0;
    const reading = previous ?? (anchor.current?.key === base ? anchor.current.reading : target.reading);
    setBusy(key); setFailure(null);
    try {
      const raw = await api<unknown>(`/impact?${query(impactRequest(target, options, collection, offset, reading))}`, undefined, controller.signal);
      if (controller.signal.aborted || current.current !== key) return;
      const parsed = parseImpactPage(raw, target, options, collection, offset, reading);
      anchor.current = { key: base, reading: parsed };
      setResponse({ key, data: previous ? appendImpactPage(previous, parsed) : parsed });
    } catch (cause) {
      if (controller.signal.aborted || current.current !== key) return;
      setFailure({ key, message: cause instanceof Error ? cause.message : '下游查询读取失败' }); onError?.(cause);
    } finally { if (current.current === key && pending.current === controller) setBusy(null); }
  }
  return <details className="impact-panel"><summary>下游影响 · 只读核对</summary>
    <p className="small muted">沿当前对象的完整范围和双时间查询依赖；结果只列需核对的记录，不改变科学状态。</p>
    <div className="impact-options"><label><input type="checkbox" checked={options.candidates} onChange={event => setOptions({ ...options, candidates: event.target.checked })} />包含候选关系</label><label><input type="checkbox" checked={options.reported} onChange={event => setOptions({ ...options, reported: event.target.checked })} />包含报告依赖（潜在）</label></div>
    <p className="small muted">{target.scope === undefined ? '沿当前视图的所有范围，未限定。' : `范围：${scopeText(target.scope)}`}{options.reported && ' 报告关联不证明运行实际读写，实际 I/O 完整性未知。'}</p>
    <div className="impact-actions"><button className="button secondary" disabled={busy === key} onClick={() => { void load(); }}>{busy === key ? '正在读取…' : collection === 'affected' ? '读取下游核对项' : '读取关系路径'}</button>{data && <button className="text-button" onClick={() => setCollection(collection === 'affected' ? 'paths' : 'affected')}>{collection === 'affected' ? '核对关系路径' : '返回下游对象'}</button>}</div>
    {error && <p className="error-message" role="alert">{error} 请主动重新读取；未自动重试或改变记录。</p>}
    {data && <section aria-label="下游影响结果"><p><strong>{data.affected_total} 个下游对象 · {data.paths_total} 条关系路径</strong></p><p className="small muted">修订 {data.revision} · 发生截止 <DateText value={data.occurred_until} /> · 获知截止 <DateText value={data.known_until} /></p>
      <p className="small muted">{data.traversal_complete ? '已遍历可查询的登记关系。' : '遍历达到深度或数量限制，结果尚不完整。'} 未登记的依赖及实际 I/O 仍未知。</p>
      {!data.items.length && <p className="missing-note">此条件下没有查到下游{collection === 'paths' ? '路径' : '对象'}，不证明研究结果没有依赖。</p>}
      <ol className="impact-results">{data.items.map(value => {
        if (collection === 'affected') {
          const item = value as ImpactItem;
          return <li key={item.node_id}><strong>{names[item.kind] ?? item.kind} · {item.labels?.join(' / ') || item.entity_id || item.version_id || item.run_id || item.request_id || item.node_id}</strong><p className="small">{item.impact_basis === 'confirmed_relation' ? '已确认关系可达' : '潜在依赖'} · 深度 {item.depth} · {scopeText(item.scope)}</p>
            {item.content_claim_ids?.map(id => onClaim && <button className="text-button" key={id} onClick={() => onClaim(id, data)}>查看内容记录 #{id}</button>)}{item.layer === 'L1' && (onNode ? <button className="text-button" onClick={() => onNode(item.node_id)}>查看登记节点详情</button> : <details><summary>查看登记节点标识</summary><p className="small mono">{item.node_id}</p><p className="small">运行 ID：{item.run_id ?? '未知'} · 请求 ID：{item.request_id ?? '未知'} · 版本 ID：{item.version_id ?? '未知'}</p></details>)}
          </li>;
        }
        const path = value as ImpactPath;
        return <li key={JSON.stringify(path)}><strong>{relations[path.relation] ?? path.relation} · {path.potential ? '潜在依赖' : '已确认关系'}</strong><p className="small mono">{path.source} → {path.target}</p><p className="small">{scopeText(path.scope)} · 依据 {path.basis ?? '未知'}</p>{path.claim_id != null && onClaim && <button className="text-button" onClick={() => onClaim(path.claim_id!, data)}>查看关系记录 #{path.claim_id}</button>}</li>;
      })}</ol>
      <p className="small muted">已加载 {data.items.length}/{data.total} {collection === 'affected' ? '个对象' : '条路径'}。</p>{data.next_offset != null && <button className="button secondary" disabled={busy === key} onClick={() => { void load(true); }}>读取更多下游{collection === 'affected' ? '对象' : '路径'}</button>}
    </section>}
  </details>;
}
