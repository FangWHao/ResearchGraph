import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api } from './api';
import { fmtTime } from './format';
import { Icon, KindGlyph } from './Icons';
import type { Message, ProjectData, SearchResult } from './types';

const ROLE: Record<Message['role'], string> = { user: '用户', assistant: 'Agent', tool: '工具' };

// 把命中的词标出来；词按字面匹配，英文不区分大小写
export function highlight(text: string, terms: string[]): ReactNode {
  const words = terms.filter(Boolean);
  if (!words.length) return text;
  const pattern = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
  return text.split(pattern).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part));
}

interface Props {
  data: ProjectData;
  // 结果里出现的事件；没有在搜索时为 null
  onMatches: (ids: Set<number> | null) => void;
  onPick: (eventId: number, focus: number | undefined, terms: string[]) => void;
}

export function SearchBox({ data, onMatches, onPick }: Props) {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SearchResult | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const events = new Map(data.events.map((e) => [e.id, e]));
  const projectId = data.project.id;

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResult(null);
      onMatches(null);
      return;
    }
    let alive = true;
    setBusy(true);
    const timer = window.setTimeout(async () => {
      try {
        const next = await api.search(projectId, q);
        if (!alive) return;
        setResult(next);
        onMatches(new Set([...next.events, ...next.messages.map((m) => m.event)]));
      } catch {
        if (alive) setResult(null);
      } finally {
        if (alive) setBusy(false);
      }
    }, 220);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [query, projectId]);

  // “/” 聚焦搜索框
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (e.key === '/') {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('pointerdown', close);
    return () => window.removeEventListener('pointerdown', close);
  }, [open]);

  const pick = (eventId: number, focus?: number) => {
    onPick(eventId, focus, result?.terms ?? []);
    setOpen(false);
  };
  const found = result ? result.events.filter((id) => events.has(id)) : [];
  const quotes = result ? result.messages.filter((m) => events.has(m.event)) : [];
  const clear = () => {
    setQuery('');
    input.current?.blur();
    setOpen(false);
  };

  return (
    <div className="search" ref={root}>
      <span className="search-icon">
        <Icon name="search" size={15} />
      </span>
      <input
        ref={input}
        className="search-input"
        type="search"
        placeholder="搜索事件和原话"
        aria-label="搜索事件和原话（快捷键 /）"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => query && setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') clear();
          if (e.key === 'Enter') {
            const first = found[0] ?? quotes[0]?.event;
            if (first !== undefined) pick(first, found[0] === undefined ? quotes[0].idx : undefined);
          }
        }}
      />
      {query ? (
        <button type="button" className="search-clear" aria-label="清空搜索" onClick={clear}>
          <Icon name="close" size={13} />
        </button>
      ) : (
        <kbd className="search-key">/</kbd>
      )}
      {open && query.trim() ? (
        <div className="search-results" role="listbox">
          {busy && !result ? <div className="search-empty muted">搜索中…</div> : null}
          {result && !found.length && !quotes.length ? (
            <div className="search-empty muted">没有找到「{query.trim()}」。几个词用空格分开时，要都出现才算。</div>
          ) : null}
          {found.length ? (
            <section>
              <div className="search-group">事件 · {found.length}</div>
              {found.map((id) => {
                const e = events.get(id)!;
                return (
                  <button key={id} type="button" className="search-item" onClick={() => pick(id)}>
                    <KindGlyph kind={e.kind} />
                    <span className="search-item-main">
                      <span className="search-item-title">{highlight(e.title, result!.terms)}</span>
                      {e.summary ? <span className="search-item-sub">{highlight(e.summary, result!.terms)}</span> : null}
                    </span>
                    <span className="search-item-time">{fmtTime(e.ts)}</span>
                  </button>
                );
              })}
            </section>
          ) : null}
          {quotes.length ? (
            <section>
              <div className="search-group">
                原话 · {quotes.length}
                {result?.more ? '+' : ''}
              </div>
              {quotes.map((m) => {
                const owner = events.get(m.event)!;
                return (
                  <button key={`${m.session_id}-${m.idx}`} type="button" className="search-item" onClick={() => pick(m.event, m.idx)}>
                    <span className="search-role">{ROLE[m.role]}</span>
                    <span className="search-item-main">
                      <span className="search-item-quote">{highlight(m.snippet, result!.terms)}</span>
                      <span className="search-item-sub">
                        在「{owner.title}」附近
                      </span>
                    </span>
                    <span className="search-item-time">{fmtTime(m.ts)}</span>
                  </button>
                );
              })}
              {result?.more ? <div className="search-empty muted small">原话太多，只列出前 60 条。换个更具体的词试试。</div> : null}
            </section>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
