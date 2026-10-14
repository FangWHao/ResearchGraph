import { useState, type ReactNode } from 'react';
import { api } from './api';
import { Segmented, Switch } from './Controls';
import type { AppState, AutoSettings } from './types';
import { TOOL_LABEL } from './types';
import { UsagePanel } from './Usage';

interface Props {
  state: AppState;
  first: boolean;
  onDone: (state: AppState) => void;
  onCancel?: () => void;
}

export function Setup({ state, first, onDone, onCancel }: Props) {
  const known = new Set(state.detected.map((d) => d.path));
  const [chosen, setChosen] = useState<Set<string>>(
    () => new Set(first && state.sources.length === 0 ? state.detected.map((d) => d.path) : state.sources),
  );
  const [extra, setExtra] = useState<string[]>(state.sources.filter((s) => !known.has(s)));
  const [manual, setManual] = useState('');
  const [key, setKey] = useState('');
  const [advanced, setAdvanced] = useState(false);
  const [baseUrl, setBaseUrl] = useState(state.model.base_url);
  const [model, setModel] = useState(state.model.model);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<{ ok: boolean; message: string } | 'running' | null>(null);
  const [auto, setAuto] = useState<AutoSettings>(state.auto);
  const [tab, setTab] = useState<'general' | 'usage'>('general');
  const changeAuto = (patch: Partial<AutoSettings>) => setAuto({ ...auto, ...patch });

  const test = async () => {
    setCheck('running');
    try {
      setCheck(
        await api.checkModel({
          ...(key.trim() ? { api_key: key.trim() } : {}),
          base_url: baseUrl.trim(),
          model: model.trim(),
        }),
      );
    } catch (e) {
      setCheck({ ok: false, message: e instanceof Error ? e.message : String(e) });
    }
  };

  const toggle = (path: string) => {
    const next = new Set(chosen);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    setChosen(next);
  };

  const add = () => {
    const path = manual.trim();
    if (!path) return;
    if (!known.has(path) && !extra.includes(path)) setExtra([...extra, path]);
    setChosen(new Set(chosen).add(path));
    setManual('');
  };

  const save = async () => {
    setBusy(true);
    setError('');
    try {
      const next = await api.saveSettings({
        sources: [...chosen],
        ...(key.trim() ? { api_key: key.trim() } : {}),
        base_url: baseUrl.trim(),
        model: model.trim(),
        ...(first ? {} : { auto }),
      });
      onDone(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <header className="page-head">
        {first ? (
          <ol className="steps">
            <li className="on"><b>1</b>找到会话</li>
            <li><b>2</b>新建项目</li>
            <li><b>3</b>整理成时间线</li>
          </ol>
        ) : null}
        <h1>{first ? '欢迎使用 ResearchGraph' : '设置'}</h1>
        {first ? <p>把 Claude Code 和 Codex 的会话整理成一张研究时间线。先告诉它会话在哪里。</p> : null}
        {first ? null : (
          <div className="tabs" role="tablist" aria-label="设置分类">
            {(
              [
                ['general', '常规'],
                ['usage', '用量'],
              ] as const
            ).map(([key, text]) => (
              <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? 'on' : ''} onClick={() => setTab(key)}>
                {text}
              </button>
            ))}
          </div>
        )}
      </header>

      {tab === 'usage' ? (
        <>
          <UsagePanel />
          <div className="row end">
            <button type="button" className="btn" onClick={onCancel}>
              返回
            </button>
          </div>
        </>
      ) : (
        <>
          <section className="card">
            <h2>会话目录</h2>
            <p>只读取这些目录里的会话文件，不会修改它们。</p>
            <div className="list">
              {state.detected.length === 0 && extra.length === 0 ? (
                <div className="muted">没有在常见位置找到 Claude Code 或 Codex 的会话，可以在下面手动填写目录。</div>
              ) : null}
              {state.detected.map((dir) => (
                <label key={dir.path} className={`choice${chosen.has(dir.path) ? ' on' : ''}`}>
                  <input type="checkbox" checked={chosen.has(dir.path)} onChange={() => toggle(dir.path)} />
                  <span className="choice-body">
                    <strong>
                      {TOOL_LABEL[dir.tool]} · {dir.files} 个会话文件
                    </strong>
                    <span className="mono muted">{dir.path}</span>
                  </span>
                </label>
              ))}
              {extra.map((path) => (
                <label key={path} className={`choice${chosen.has(path) ? ' on' : ''}`}>
                  <input type="checkbox" checked={chosen.has(path)} onChange={() => toggle(path)} />
                  <span className="choice-body">
                    <strong>手动添加</strong>
                    <span className="mono muted">{path}</span>
                  </span>
                </label>
              ))}
            </div>
            <div className="row">
              <input
                className="input"
                placeholder="其他目录，例如 /mnt/c/Users/me/.claude/projects"
                value={manual}
                onChange={(e) => setManual(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && add()}
              />
              <button type="button" className="btn" onClick={add}>
                添加
              </button>
            </div>
          </section>

          <section className="card">
            <h2>整理用的模型</h2>
            <p>自动整理会把会话里的文字发送给模型。每个项目第一次整理前都会先告诉你发送量，你确认后才会发送。</p>
            <label className="field">
              <span>DeepSeek API Key{state.model.has_key ? '（已设置，留空表示不修改）' : ''}</span>
              <div className="row">
                <input
                  className="input"
                  type="password"
                  autoComplete="off"
                  placeholder={state.model.has_key ? '••••••••' : 'sk-…'}
                  value={key}
                  onChange={(e) => {
                    setKey(e.target.value);
                    setCheck(null);
                  }}
                />
                <button
                  type="button"
                  className="btn"
                  disabled={check === 'running' || (!key.trim() && !state.model.has_key)}
                  onClick={test}
                >
                  {check === 'running' ? '检测中…' : '检测'}
                </button>
              </div>
            </label>
            {check && check !== 'running' ? (
              <div className={`small ${check.ok ? 'ok' : 'error'}`}>
                {check.ok ? '✓ ' : '✗ '}
                {check.message}
              </div>
            ) : null}
            <button type="button" className="link small" onClick={() => setAdvanced(!advanced)}>
              {advanced ? '收起高级设置' : '高级设置（换接口或模型）'}
            </button>
            {advanced ? (
              <>
                <label className="field">
                  <span>接口地址（Anthropic Messages 兼容）</span>
                  <input
                    className="input"
                    value={baseUrl}
                    onChange={(e) => {
                      setBaseUrl(e.target.value);
                      setCheck(null);
                    }}
                  />
                </label>
                <label className="field">
                  <span>模型</span>
                  <input
                    className="input"
                    value={model}
                    onChange={(e) => {
                      setModel(e.target.value);
                      setCheck(null);
                    }}
                  />
                </label>
              </>
            ) : null}
          </section>

          {first ? null : (
            <section className="card">
              <div className="card-head">
                <div>
                  <h2>自动更新</h2>
                  <p>项目页面开着时，会话有了新内容并安静一段时间后，自动整理新增的部分。只对已经同意过发送的项目生效。</p>
                </div>
                <Switch checked={auto.enabled} label="打开的项目自动更新" onChange={(enabled) => changeAuto({ enabled })} />
              </div>
              <Option title="会话安静多久后更新" hint="对话还在进行时不打断，等它停下来再整理。" disabled={!auto.enabled}>
                <Segmented
                  label="会话安静多久后更新"
                  showLabel={false}
                  disabled={!auto.enabled}
                  value={auto.quiet}
                  options={[
                    [1, '1 分钟'],
                    [3, '3 分钟'],
                    [5, '5 分钟'],
                    [10, '10 分钟'],
                  ]}
                  onChange={(quiet) => changeAuto({ quiet })}
                />
              </Option>
              <Option title="单次自动更新的上限" hint="一次要发送的新内容超过这个量时，先问你。" disabled={!auto.enabled}>
                <Segmented
                  label="单次自动更新的上限"
                  showLabel={false}
                  disabled={!auto.enabled}
                  value={auto.limit}
                  options={[
                    [50_000, '5 万字'],
                    [200_000, '20 万字'],
                    [1_000_000, '100 万字'],
                    [null, '不限'],
                  ]}
                  onChange={(limit) => changeAuto({ limit })}
                />
              </Option>
              <Option title="打开项目时发现离开期间的新内容" hint="没打开的项目不会在后台更新，等你下次打开时再处理。">
                <Segmented
                  label="打开项目时发现离开期间的新内容"
                  showLabel={false}
                  value={auto.on_open}
                  options={[
                    ['ask', '先问我'],
                    ['auto', '直接更新'],
                  ]}
                  onChange={(on_open) => changeAuto({ on_open })}
                />
              </Option>
            </section>
          )}

          {error ? <div className="error">{error}</div> : null}
          <div className="row end">
            {onCancel ? (
              <button type="button" className="btn" onClick={onCancel}>
                取消
              </button>
            ) : null}
            <button type="button" className="btn primary" disabled={busy || chosen.size === 0} onClick={save}>
              {first ? '下一步' : '保存'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function Option({ title, hint, disabled = false, children }: { title: string; hint: string; disabled?: boolean; children: ReactNode }) {
  return (
    <div className={`option${disabled ? ' disabled' : ''}`}>
      <div className="option-text">
        <span className="option-title">{title}</span>
        <span className="option-hint">{hint}</span>
      </div>
      {children}
    </div>
  );
}
