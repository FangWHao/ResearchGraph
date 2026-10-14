import { useEffect, useState } from 'react';
import { api } from './api';
import { fmtFull, fmtTime, fmtTokens } from './format';
import type { UsageBucket, UsageReport } from './types';

const PERIODS: [keyof UsageReport['periods'], string][] = [
  ['today', '今天'],
  ['week', '近 7 天'],
  ['month', '近 30 天'],
  ['all', '全部'],
];
const STAGE_LABEL = { extract: '提取关键步骤', threads: '整理研究线' };

const total = (b: UsageBucket) => b.input + b.output;

function shortDay(day: string): string {
  const [, m, d] = day.split('-').map(Number);
  return `${m}月${d}日`;
}

export function UsagePanel() {
  const [data, setData] = useState<UsageReport | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.usage().then(setData, (e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (error) return <div className="error">{error}</div>;
  if (!data) return <div className="muted">加载中…</div>;
  const all = data.periods.all;
  if (!data.since) {
    return (
      <section className="card">
        <h2>还没有用量</h2>
        <p>整理项目时每调用一次模型都会记下用掉的 token，之后在这里按时间、项目和步骤汇总。</p>
      </section>
    );
  }

  return (
    <>
      <section className="card">
        <h2>用掉的 token</h2>
        <div className="stats">
          {PERIODS.map(([key, label]) => {
            const b = data.periods[key];
            return (
              <div key={key} className="stat">
                <span className="stat-label">
                  {label}
                  <span>{b.calls} 次调用</span>
                </span>
                <strong className="stat-value">{fmtTokens(total(b))}</strong>
                <span className="stat-sub">输入 {fmtTokens(b.input)}</span>
                <span className="stat-sub">输出 {fmtTokens(b.output)}</span>
              </div>
            );
          })}
        </div>
        <DailyBars days={data.days} />
      </section>

      <section className="card">
        <h2>按项目</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>项目</th>
                <th className="num">调用</th>
                <th className="num">输入</th>
                <th className="num">输出</th>
                <th className="num">合计</th>
                <th>最近一次</th>
              </tr>
            </thead>
            <tbody>
              {data.projects.map((p) => (
                <tr key={p.id ?? 'deleted'}>
                  <td className={p.name ? '' : 'muted'}>{p.name ?? '已删除的项目'}</td>
                  <td className="num">
                    {p.calls}
                    {p.cached ? <span className="muted small"> +{p.cached} 缓存</span> : null}
                  </td>
                  <td className="num">{fmtTokens(p.input)}</td>
                  <td className="num">{fmtTokens(p.output)}</td>
                  <td className="num strong">{fmtTokens(total(p))}</td>
                  <td className="muted">{fmtTime(p.last)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h2>按步骤</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>步骤</th>
                <th className="num">调用</th>
                <th className="num">输入</th>
                <th className="num">输出</th>
                <th className="num">合计</th>
              </tr>
            </thead>
            <tbody>
              {data.stages.map((s) => (
                <tr key={s.stage}>
                  <td>{STAGE_LABEL[s.stage] ?? s.stage}</td>
                  <td className="num">{s.calls}</td>
                  <td className="num">{fmtTokens(s.input)}</td>
                  <td className="num">{fmtTokens(s.output)}</td>
                  <td className="num strong">{fmtTokens(total(s))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <ul className="notes">
        <li>token 数以模型接口的返回为准；输入包括提示词和会话文字。</li>
        {all.cache_read ? (
          <li>输入里有 {fmtTokens(all.cache_read)} 命中了模型服务端的缓存，通常按更低的价格计费。</li>
        ) : null}
        {all.cached ? <li>另有 {all.cached} 次请求和以前完全相同，直接用了本地保存的结果，没有花 token。</li> : null}
        <li>从 {fmtFull(data.since)} 开始记录，这之前的调用没有记下 token 数。</li>
      </ul>
    </>
  );
}

function DailyBars({ days }: { days: UsageReport['days'] }) {
  const [hover, setHover] = useState<number | null>(null);
  const values = days.map(total);
  const max = Math.max(0, ...values);
  if (max === 0) return <p className="muted small">近 30 天没有调用模型。</p>;
  const tip = hover === null ? null : days[hover];
  return (
    <figure className="bars">
      <figcaption>近 30 天每天的 token</figcaption>
      <div className="bars-plot" onPointerLeave={() => setHover(null)}>
        <span className="bars-max">{fmtTokens(max)}</span>
        {days.map((d, i) => (
          <div
            key={d.day}
            className={`bars-col${hover === i ? ' on' : ''}`}
            tabIndex={0}
            aria-label={`${shortDay(d.day)}：${fmtTokens(values[i])} token，${d.calls} 次调用`}
            onPointerEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
            onBlur={() => setHover(null)}
          >
            {values[i] ? <div className="bars-bar" style={{ height: `max(2px, ${(values[i] / max) * 100}%)` }} /> : null}
          </div>
        ))}
        {tip && hover !== null ? (
          <div
            className="bars-tip"
            // 放在柱子旁边、图内顶部，不压住上面的数字；后半段放到柱子左边
            style={
              hover < days.length / 2
                ? { left: `calc(${((hover + 1) / days.length) * 100}% + 6px)` }
                : { left: `calc(${(hover / days.length) * 100}% - 6px)`, transform: 'translateX(-100%)' }
            }
            role="tooltip"
          >
            <strong>{shortDay(tip.day)}</strong>
            <span>合计 {fmtTokens(total(tip))}</span>
            <span className="muted">
              输入 {fmtTokens(tip.input)} · 输出 {fmtTokens(tip.output)}
            </span>
            <span className="muted">{tip.calls} 次调用</span>
          </div>
        ) : null}
      </div>
      <div className="bars-axis">
        <span>{shortDay(days[0].day)}</span>
        <span>{shortDay(days[Math.floor(days.length / 2)].day)}</span>
        <span>今天</span>
      </div>
    </figure>
  );
}
