const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
const pad = (n: number) => String(n).padStart(2, '0');

function parse(ts: string | null | undefined): Date | null {
  if (!ts) return null;
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function fmtTime(ts: string | null | undefined): string {
  const d = parse(ts);
  return d ? `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}` : '时间未知';
}

export function fmtClock(ts: string | null | undefined): string {
  const d = parse(ts);
  return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
}

export function fmtFull(ts: string | null | undefined): string {
  const d = parse(ts);
  return d
    ? `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${WEEK[d.getDay()]} ${pad(d.getHours())}:${pad(d.getMinutes())}`
    : '时间未知';
}

export function fmtDayKey(key: string): string {
  if (!key) return '时间未知';
  const [y, m, d] = key.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  return `${m}月${d}日 ${WEEK[date.getDay()]}`;
}

export function fmtChars(n: number): string {
  return n >= 10000 ? `约 ${(n / 10000).toFixed(1)} 万字` : `约 ${n} 字`;
}

export function fmtSize(bytes: number): string {
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

export function basename(path: string): string {
  return path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path;
}
