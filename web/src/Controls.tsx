// 分段选择和开关：顶栏、设置页共用
export function Segmented<T extends string | number | null>({
  label,
  value,
  options,
  onChange,
  disabled = false,
  showLabel = true,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onChange: (value: T) => void;
  disabled?: boolean;
  showLabel?: boolean;
}) {
  return (
    <div className={`segmented${disabled ? ' disabled' : ''}`} role="radiogroup" aria-label={label}>
      {showLabel ? <span className="segmented-label">{label}</span> : null}
      {options.map(([key, text]) => (
        <button
          key={String(key)}
          type="button"
          role="radio"
          aria-checked={value === key}
          className={value === key ? 'on' : ''}
          disabled={disabled}
          onClick={() => onChange(key)}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

export function Switch({ checked, label, onChange }: { checked: boolean; label: string; onChange: (checked: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`switch${checked ? ' on' : ''}`}
      onClick={() => onChange(!checked)}
    >
      <span />
    </button>
  );
}
