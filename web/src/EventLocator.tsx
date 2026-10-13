import { useState } from 'react';
import { eventNumber } from './l1';

export function EventLocator({ onEvidence }: { onEvidence: (target: { event_id: number }) => void }) {
  const [value, setValue] = useState('');
  const [failure, setFailure] = useState('');
  return <form className="event-locator" onSubmit={event => {
    event.preventDefault();
    const id = eventNumber(value);
    if (id == null) { setFailure('请输入有效的正整数事件编号。'); return; }
    setFailure(''); onEvidence({ event_id: id });
  }}>
    <div><label htmlFor="event-locator-id">全库事件编号</label><p className="muted small">编号跨项目；可打开命令报告或证据引用中的原始事件。正文检索仍按当前项目过滤。</p></div>
    <div className="event-locator-actions"><input id="event-locator-id" inputMode="numeric" value={value} onChange={event => { setValue(event.target.value); setFailure(''); }} placeholder="例如 21" />
      <button type="submit" className="button secondary">按编号打开原文</button></div>
    {failure && <p className="error-message" role="alert">{failure}</p>}
  </form>;
}
