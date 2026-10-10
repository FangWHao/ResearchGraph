import type { Claim } from './types';

export function exactInstant(value: unknown): bigint | null {
  if (typeof value !== 'string') return null;
  const p = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?(Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!p || p[0] !== value) return null;
  const year = Number(p[1]), month = Number(p[2]), day = Number(p[3]);
  const hour = Number(p[4]), minute = Number(p[5]), second = Number(p[6] ?? 0);
  const offsetHour = Number(p[10] ?? 0), offsetMinute = Number(p[11] ?? 0);
  const days = [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1] || hour > 23 || minute > 59 || second > 59 || offsetHour > 23 || offsetMinute > 59) return null;
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day); date.setUTCHours(hour, minute, second, 0);
  const offset = (p[9] === '-' ? -1 : 1) * (offsetHour * 60 + offsetMinute);
  const ms = date.getTime() - offset * 60000;
  const utcYear = new Date(ms).getUTCFullYear();
  if (utcYear < 1 || utcYear > 9999) return null;
  return BigInt(ms) * 1000n + BigInt((p[7] ?? '').padEnd(6, '0'));
}
export function claimInstant(claim: Claim, axis: 'occurred' | 'recorded'): bigint | null {
  const canonical = `${axis}_at_utc` as const;
  if (Object.prototype.hasOwnProperty.call(claim, canonical)) {
    const value = claim[canonical];
    return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}(?:Z|\+00:00)$/.test(value) ? exactInstant(value) : null;
  }
  return exactInstant(claim[`${axis}_at`]);
}
export function compareInstants(before: bigint, after: bigint): number { return before < after ? -1 : before > after ? 1 : 0; }
