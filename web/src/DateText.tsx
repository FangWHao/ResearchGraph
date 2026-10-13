export function DateText({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">时间未知</span>;
  const parsed = new Date(value);
  return <time dateTime={value} title={value}>{Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })}</time>;
}
