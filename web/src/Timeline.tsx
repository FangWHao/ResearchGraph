import { useMemo } from 'react';
import { fmtDayKey } from './format';
import { computeLayout, NODE_H, NODE_W, type Lane, type TimelineInput, type ViewMode } from './layout';
import { KIND_LABEL } from './types';

const HEADER_W = 'clamp(120px, 28vw, 210px)';
const AXIS_H = 38;

export type Selection = { type: 'event'; id: number } | { type: 'lane'; lane: Lane } | null;

interface Props {
  data: TimelineInput;
  mode: ViewMode;
  selection: Selection;
  onSelect: (selection: Selection) => void;
}

export function Timeline({ data, mode, selection, onSelect }: Props) {
  const layout = useMemo(() => computeLayout(data, mode), [data, mode]);
  const selectedEvent = selection?.type === 'event' ? selection.id : null;
  const selectedLane = selection?.type === 'lane' ? selection.lane.id : null;

  return (
    <div className="tl-scroll" onClick={() => onSelect(null)}>
      <div
        className="tl-grid"
        style={{
          gridTemplateColumns: `${HEADER_W} ${layout.width}px`,
          gridTemplateRows: `${AXIS_H}px ${layout.height}px`,
        }}
      >
        <div className="tl-corner">{mode === 'threads' ? '研究线' : '会话'}</div>
        <div className="tl-axis">
          {layout.days.map((day) => (
            <div key={`${day.key}:${day.x}`} className="tl-day" style={{ left: day.x, width: day.width }}>
              {fmtDayKey(day.key)}
            </div>
          ))}
        </div>
        <div className="tl-labels">
          {layout.lanes.map((lane) => (
            <button
              key={lane.id}
              type="button"
              className={`tl-lane-label${selectedLane === lane.id ? ' on' : ''}`}
              style={{ top: lane.y, height: lane.height }}
              onClick={(e) => {
                e.stopPropagation();
                onSelect({ type: 'lane', lane });
              }}
            >
              <span className="tl-lane-title">{lane.title}</span>
              <span className="tl-lane-sub">{lane.subtitle}</span>
              {lane.rows.slice(1).map((row) => (
                <span key={row.id} className="tl-row-label" style={{ top: row.y - lane.y + 30 }}>
                  ↳ {row.label}
                </span>
              ))}
            </button>
          ))}
        </div>
        <div className="tl-canvas">
          {layout.lanes.map((lane) => (
            <div key={lane.id} className="tl-lane-bg" style={{ top: lane.y, height: lane.height }} />
          ))}
          {layout.lanes.flatMap((lane) =>
            lane.rows.slice(1).map((row) => <div key={row.id} className="tl-row-line" style={{ top: row.y }} />),
          )}
          {layout.days.slice(1).map((day) => (
            <div key={`sep:${day.x}`} className="tl-daysep" style={{ left: day.x }} />
          ))}
          <svg className="tl-edges" width={layout.width} height={layout.height}>
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
                <path d="M0,0 L10,5 L0,10 z" fill="var(--edge)" />
              </marker>
              <marker id="arrow-on" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
                <path d="M0,0 L10,5 L0,10 z" fill="var(--accent)" />
              </marker>
            </defs>
            {layout.edges.map((edge) => {
              const on = selectedEvent !== null && (edge.from === selectedEvent || edge.to === selectedEvent);
              return (
                <path
                  key={edge.id}
                  className={`tl-edge ${edge.kind}${on ? ' on' : ''}`}
                  d={edge.path}
                  markerEnd={on ? 'url(#arrow-on)' : 'url(#arrow)'}
                />
              );
            })}
          </svg>
          {layout.nodes.map(({ event, x, y }) => {
            const on = event.id === selectedEvent;
            return (
              <button
                key={event.id}
                type="button"
                title={`${KIND_LABEL[event.kind]}：${event.title}\n${event.summary}`}
                className={`tl-node kind-${event.kind}${on ? ' on' : ''}`}
                style={{ left: x, top: y, width: NODE_W, height: NODE_H }}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect({ type: 'event', id: event.id });
                }}
              >
                <span className="tl-node-kind">
                  {KIND_LABEL[event.kind]}
                  {event.edited ? ' · 已改' : ''}
                </span>
                <span className="tl-node-title">{event.title}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
