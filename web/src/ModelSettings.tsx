import { useCallback, useEffect, useState } from 'react';
import { api, type CheckResult } from './api';
import { fmtTokens } from './format';
import type { ModelInfo, Stage, StageModel, Thinking } from './types';
import { STAGES, THINKING_LABEL } from './types';

const STAGE_TEXT: Record<Stage, { title: string; hint: string }> = {
  extract: {
    title: '提取关键步骤',
    hint: '每段对话调用一次，次数最多，只需读懂这一段',
  },
  threads: {
    title: '归入研究线',
    hint: '每批新事件一次，研究线多时再合并一次，要看整个项目；思考容易超长，建议不开',
  },
  review: {
    title: '复审研究线',
    hint: '每条研究线一次：挑里程碑、写进展、标状态；最后写主页上的项目简报',
  },
};

// 推荐设置：在真实项目的副本上比较过（见 DECISIONS）。提取调用最多，用 flash 加思考：
// 合并得好又不漏；归类和复审看整个项目，用 pro。归类打开思考后输出总被截断，所以不思考
export const RECOMMENDED: Record<Stage, StageModel> = {
  extract: { model: 'deepseek-flash', thinking: 'high' },
  threads: { model: 'deepseek-v4-pro', thinking: 'off' },
  review: { model: 'deepseek-v4-pro', thinking: 'off' },
};

interface Props {
  hasKey: boolean;
  apiKey: string;
  baseUrl: string;
  stages: Record<Stage, StageModel>;
  onStages: (stages: Record<Stage, StageModel>) => void;
}

export function StageModels({ hasKey, apiKey, baseUrl, stages, onStages }: Props) {
  const [list, setList] = useState<ModelInfo[] | null>(null);
  const [listError, setListError] = useState('');
  const [loading, setLoading] = useState(false);
  const [manual, setManual] = useState<Set<Stage>>(new Set());
  const [check, setCheck] = useState<CheckResult | 'running' | null>(null);

  const load = useCallback(async () => {
    if (!hasKey && !apiKey.trim()) return;
    setLoading(true);
    try {
      const result = await api.listModels({
        base_url: baseUrl.trim() || undefined,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      setList(result.models.length ? result.models : null);
      setListError(result.error ?? '');
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [hasKey, apiKey, baseUrl]);

  useEffect(() => {
    void load();
    // 只在打开时自动读一次；改了 Key 或接口后点“刷新”
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const info = (id: string) => list?.find((m) => m.id === id);
  const change = (stage: Stage, patch: Partial<StageModel>) => {
    const next = { ...stages[stage], ...patch };
    // 换了模型，原来的思考强度它不支持就回到“不思考”
    const levels = info(next.model)?.thinking;
    if (levels && next.thinking !== 'off' && !levels.includes(next.thinking)) next.thinking = 'off';
    onStages({ ...stages, [stage]: next });
    setCheck(null);
  };
  const test = async () => {
    setCheck('running');
    try {
      setCheck(
        await api.checkModel({
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
          base_url: baseUrl.trim() || undefined,
          models: stages,
        }),
      );
    } catch (e) {
      setCheck({ ok: false, message: e instanceof Error ? e.message : String(e) });
    }
  };
  const chosen = [...new Set(STAGES.map((s) => stages[s].model))];

  return (
    <div className="stage-models">
      <div className="stage-head">
        <span className="field-label">按任务选择模型</span>
        <span className="spacer" />
        <button type="button" className="link small" disabled={loading} onClick={() => void load()}>
          {loading ? '读取模型列表…' : '刷新模型列表'}
        </button>
        <button type="button" className="link small" onClick={() => onStages({ ...RECOMMENDED })}>
          用推荐设置
        </button>
      </div>
      {listError ? <div className="muted small">{listError}</div> : null}
      <div className="stage-table" role="table" aria-label="各任务用的模型">
        {STAGES.map((stage) => {
          const value = stages[stage];
          const meta = info(value.model);
          const levels: Thinking[] = meta ? meta.thinking : ['low', 'high', 'max'];
          const typing = manual.has(stage) || !list;
          return (
            <div key={stage} className="stage-row" role="row">
              <div className="stage-text" role="cell">
                <strong>{STAGE_TEXT[stage].title}</strong>
                <span className="muted small">{STAGE_TEXT[stage].hint}</span>
              </div>
              <div role="cell">
                {typing ? (
                  <input
                    className="input"
                    aria-label={`${STAGE_TEXT[stage].title}的模型`}
                    value={value.model}
                    onChange={(e) => change(stage, { model: e.target.value })}
                  />
                ) : (
                  <select
                    className="select"
                    aria-label={`${STAGE_TEXT[stage].title}的模型`}
                    value={value.model}
                    onChange={(e) => {
                      if (e.target.value === '__manual__') setManual(new Set(manual).add(stage));
                      else change(stage, { model: e.target.value });
                    }}
                  >
                    {list!.map((m) => (
                      <option key={m.id} value={m.id} title={m.name}>
                        {m.id}
                      </option>
                    ))}
                    {!meta ? <option value={value.model}>{value.model}（不在列表里）</option> : null}
                    <option value="__manual__">手动填写…</option>
                  </select>
                )}
              </div>
              <div role="cell">
                <select
                  className="select"
                  aria-label={`${STAGE_TEXT[stage].title}的思考强度`}
                  value={value.thinking}
                  disabled={!levels.length}
                  onChange={(e) => change(stage, { thinking: e.target.value as Thinking })}
                >
                  <option value="off">{THINKING_LABEL.off}</option>
                  {levels.map((lv) => (
                    <option key={lv} value={lv}>
                      思考：{THINKING_LABEL[lv]}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          );
        })}
      </div>
      <div className="stage-foot">
        <div className="model-facts">
          {chosen.map((id) => {
            const m = info(id);
            return (
              <span key={id} className="muted small">
                <strong className="ink">{m?.name ?? id}</strong>
                {m && m.name !== id ? <span className="fact">（{id}）</span> : null}
                {m?.context_window ? <span className="fact"> · 上下文 {fmtTokens(m.context_window)} token</span> : null}
                {m?.max_output_tokens ? <span className="fact"> · 最长输出 {fmtTokens(m.max_output_tokens)} token</span> : null}
                {m && !m.thinking.length ? <span className="fact"> · 不支持思考</span> : null}
              </span>
            );
          })}
        </div>
        <button type="button" className="btn" disabled={check === 'running' || (!hasKey && !apiKey.trim())} onClick={test}>
          {check === 'running' ? '检测中…' : '检测这些模型'}
        </button>
      </div>
      {check && check !== 'running' ? (
        <div className="check-results">
          {(check.results?.length ? check.results : [{ model: '', ok: check.ok, message: check.message }]).map((r) => (
            <div key={r.model || 'all'} className={`small ${r.ok ? 'ok' : 'error'}`}>
              {r.ok ? '✓ ' : '✗ '}
              {r.model ? <strong>{r.model}：</strong> : null}
              {r.message}
            </div>
          ))}
        </div>
      ) : null}
      <p className="muted small">
        打开思考后，每次调用会多花一些 token 用来思考，结果通常更稳，但慢一些。换了模型或思考强度，之前的缓存不会复用，重新整理时会重新调用。
      </p>
    </div>
  );
}
