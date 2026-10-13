import { useState } from 'react';
import { api } from './api';
import type { AppState } from './types';
import { TOOL_LABEL } from './types';

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
      {first ? (
        <div className="steps">
          <b>① 找到会话</b> → <span>② 新建项目</span> → <span>③ 自动整理时间线</span>
        </div>
      ) : null}
      <h1>{first ? '欢迎使用 ResearchGraph' : '设置'}</h1>

      <section className="card">
        <h2>会话目录</h2>
        <p className="muted small">
          ResearchGraph 只读取这些目录里的会话文件，不会修改它们。
        </p>
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
        <p className="muted small">
          自动整理会把会话里的文字发送给模型。每个项目第一次整理前都会先告诉你发送量，你确认后才会发送。
        </p>
        <label className="field">
          <span>DeepSeek API Key{state.model.has_key ? '（已设置，留空表示不修改）' : ''}</span>
          <input
            className="input"
            type="password"
            autoComplete="off"
            placeholder={state.model.has_key ? '••••••••' : 'sk-…'}
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
        </label>
        <button type="button" className="link small" onClick={() => setAdvanced(!advanced)}>
          {advanced ? '收起高级设置' : '高级设置（换接口或模型）'}
        </button>
        {advanced ? (
          <>
            <label className="field">
              <span>接口地址（Anthropic Messages 兼容）</span>
              <input className="input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
            </label>
            <label className="field">
              <span>模型</span>
              <input className="input" value={model} onChange={(e) => setModel(e.target.value)} />
            </label>
          </>
        ) : null}
      </section>

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
    </div>
  );
}
