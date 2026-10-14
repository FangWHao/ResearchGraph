import type { Kind } from './types';

// 事件种类：三个色相加中性墨色，再用形状区分，保证不靠颜色也能认出来。
export function KindGlyph({ kind, size = 10, ring = false }: { kind: Kind; size?: number; ring?: boolean }) {
  const s = size;
  const c = s / 2;
  // ring：时间轴上的点，外面加一圈底色，压在连线上也清楚
  const stroke = ring ? { stroke: 'var(--surface)', strokeWidth: 2, paintOrder: 'stroke' as const } : {};
  const box = ring ? s + 4 : s;
  const o = ring ? 2 : 0;
  let shape;
  switch (kind) {
    case 'question':
      shape = (
        <circle cx={o + c} cy={o + c} r={c - 1} fill="var(--surface)" stroke="var(--k-neutral)" strokeWidth={2} />
      );
      break;
    case 'attempt':
      shape = <circle cx={o + c} cy={o + c} r={c} fill="var(--k-attempt)" {...stroke} />;
      break;
    case 'finding':
      shape = (
        <path
          d={`M${o + c},${o} L${o + s},${o + c} L${o + c},${o + s} L${o},${o + c} Z`}
          fill="var(--k-finding)"
          {...stroke}
        />
      );
      break;
    case 'decision':
      shape = <rect x={o + 0.5} y={o + 0.5} width={s - 1} height={s - 1} rx={1.5} fill="var(--k-decision)" {...stroke} />;
      break;
    case 'output': {
      const points = Array.from({ length: 10 }, (_, i) => {
        const r = i % 2 === 0 ? c : c * 0.45;
        const a = (Math.PI / 5) * i - Math.PI / 2;
        return `${o + c + r * Math.cos(a)},${o + c + r * Math.sin(a)}`;
      }).join(' ');
      shape = <polygon points={points} fill="var(--k-neutral)" {...stroke} />;
      break;
    }
  }
  return (
    <svg className="glyph" width={box} height={box} viewBox={`0 0 ${box} ${box}`} aria-hidden="true">
      {shape}
    </svg>
  );
}

const PATHS: Record<string, string> = {
  plus: 'M12 5v14M5 12h14',
  refresh: 'M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  close: 'M6 6l12 12M18 6L6 18',
  edit: 'M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4',
  minus: 'M5 12h14',
  arrowLeft: 'M15 6l-6 6 6 6',
  arrowRight: 'M9 6l6 6-6 6',
  toEnd: 'M7 6l6 6-6 6M17 6v12',
  branch: 'M6 4v10a4 4 0 0 0 4 4h8M14 14l4 4-4 4',
  merge: 'M6 4v4a6 6 0 0 0 6 6h6M14 10l4 4-4 4',
  settings:
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
  alert: 'M12 9v4M12 17h.01M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  check: 'M5 12l5 5L20 7',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4',
};

export function Icon({ name, size = 16 }: { name: keyof typeof PATHS; size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={name === 'more' ? 3 : 1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

export function Logo() {
  // 三条泳道、一条分支：产品本身的样子
  return (
    <svg className="logo" width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
      <rect width="22" height="22" rx="6" fill="var(--ink)" />
      <path d="M5 8h12M5 14h4c2 0 3-6 5-6" stroke="var(--surface)" strokeWidth="1.8" fill="none" strokeLinecap="round" />
      <circle cx="9" cy="8" r="1.8" fill="var(--k-attempt)" />
      <circle cx="15" cy="8" r="1.8" fill="var(--k-decision)" />
      <circle cx="9" cy="14" r="1.8" fill="var(--k-finding)" />
    </svg>
  );
}
