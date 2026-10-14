import { useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { fmtDayKey, fmtTime } from './format';
import { Icon, KindGlyph } from './Icons';
import {
  CARD_W,
  computeLayout,
  DOT_HIT,
  LEFT_PAD,
  TITLE_PX,
  type Align,
  type Lane,
  type Measure,
  type PlacedNode,
  type TimelineInput,
  type ViewMode,
} from './layout';
import { eventLabel, KIND_LABEL, TOOL_LABEL, type Session } from './types';

const AXIS_H = 40;

// 用画布按页面实际字体量标题宽度，卡片高度才贴合；画布不可用（测试环境）时布局自己按字符估
function useMeasure(): Measure | undefined {
  return useMemo(() => {
    const ctx = document.createElement('canvas').getContext?.('2d');
    if (!ctx) return undefined;
    const family = getComputedStyle(document.body).fontFamily || 'system-ui, sans-serif';
    const cache = new Map<string, number>();
    return (text, weight) => {
      const key = `${weight}|${text}`;
      let width = cache.get(key);
      if (width === undefined) {
        ctx.font = `${weight} ${TITLE_PX}px ${family}`;
        width = ctx.measureText(text).width;
        cache.set(key, width);
      }
      return width;
    };
  }, []);
}

export type Selection = { type: 'event'; id: number } | { type: 'lane'; lane: Lane } | { type: 'overview' } | null;

interface Props {
  data: TimelineInput;
  view: ViewMode;
  align: Align;
  pxPerHour: number;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  // n 变化时滚动：latest 为真滚到最新的事件，否则回到左上角
  jump: { n: number; latest: boolean };
  // 刚才更新新增的事件，加个标记
  fresh: Set<number>;
  // 正在搜索时，结果里的事件；其余的变淡
  matches: Set<number> | null;
  // 详略过滤后，每个可见事件后面藏着几个细节
  hidden: Map<number, number>;
  // 已经展开的卡片、因为展开才露出来的卡片
  opened: Set<number>;
  revealed: Set<number>;
  onToggle: (eventId: number) => void;
}

export function Timeline({
  data,
  view,
  align,
  pxPerHour,
  selection,
  onSelect,
  jump,
  fresh,
  matches,
  hidden,
  opened,
  revealed,
  onToggle,
}: Props) {
  const measure = useMeasure();
  const layout = useMemo(
    () => computeLayout(data, { view, align, pxPerHour, measure }),
    [data, view, align, pxPerHour, measure],
  );
  const scroller = useRef<HTMLDivElement>(null);
  const labels = useRef<HTMLDivElement>(null);
  const scale = useRef(pxPerHour);
  const [hovered, setHovered] = useState<number | null>(null);
  const selectedEvent = selection?.type === 'event' ? selection.id : null;
  const selectedLane = selection?.type === 'lane' ? selection.lane.id : null;
  const sessions = useMemo(() => new Map(data.sessions.map((s) => [s.id, s])), [data.sessions]);

  const viewport = () => {
    const el = scroller.current!;
    return { el, width: el.clientWidth - (labels.current?.offsetWidth ?? 0), height: el.clientHeight - AXIS_H };
  };

  // 打开项目、切换视图时回到起点（真实时间模式滚到最新）；点“最新”时把最新的事件滚到眼前
  useLayoutEffect(() => {
    if (!scroller.current) return;
    const { el, width, height } = viewport();
    const latest = layout.nodes.at(-1);
    if (!jump.latest || !latest) {
      el.scrollLeft = 0;
      el.scrollTop = 0;
      return;
    }
    const right = align === 'compact' ? latest.x + CARD_W : latest.x + 160;
    el.scrollLeft = right + 64 - width;
    el.scrollTop = latest.y - height / 2;
  }, [jump.n]);

  // 紧凑模式下有跨泳道连线的事件，卡片上加个标记
  const crossLinked = useMemo(() => {
    const ids = new Set<number>();
    if (align !== 'compact') return ids;
    for (const edge of layout.edges) {
      if (edge.cross) {
        ids.add(edge.from);
        ids.add(edge.to);
      }
    }
    return ids;
  }, [layout.edges, align]);

  // 缩放时保持视野中心对着同一时刻
  useLayoutEffect(() => {
    const before = scale.current;
    scale.current = pxPerHour;
    if (!scroller.current || align !== 'time' || before === pxPerHour) return;
    const { el, width } = viewport();
    const center = el.scrollLeft + width / 2 - LEFT_PAD;
    el.scrollLeft = center * (pxPerHour / before) + LEFT_PAD - width / 2;
  }, [pxPerHour, align]);

  // 键盘或面板里跳转到的事件，滚进视野
  useLayoutEffect(() => {
    if (selectedEvent === null || !scroller.current) return;
    const node = layout.nodes.find((n) => n.event.id === selectedEvent);
    if (!node) return;
    const { el, width, height } = viewport();
    const [left, right] = align === 'compact' ? [node.x, node.x + CARD_W] : [node.x - DOT_HIT, node.x + 160];
    const [top, bottom] = align === 'compact' ? [node.y, node.y + node.h] : [node.y - DOT_HIT, node.y + DOT_HIT];
    if (left < el.scrollLeft + 16) el.scrollLeft = left - 48;
    else if (right > el.scrollLeft + width - 16) el.scrollLeft = right - width + 48;
    if (top < el.scrollTop + 8) el.scrollTop = top - 24;
    else if (bottom > el.scrollTop + height - 8) el.scrollTop = bottom - height + 24;
  }, [selectedEvent, layout, align]);

  // 选中的泳道（比如从主页点研究线进来）滚进视野
  useLayoutEffect(() => {
    if (selectedLane === null || !scroller.current) return;
    const lane = layout.lanes.find((l) => l.id === selectedLane);
    if (!lane) return;
    const { el, height } = viewport();
    if (lane.y < el.scrollTop || lane.y + Math.min(lane.height, height) > el.scrollTop + height) el.scrollTop = lane.y - 8;
  }, [selectedLane, layout]);

  const hoveredNode = hovered === null ? null : layout.nodes.find((n) => n.event.id === hovered) ?? null;

  return (
    <div className={`tl tl-${align}`} ref={scroller} onClick={() => onSelect(null)}>
      <div className="tl-inner" style={{ width: `calc(var(--label-w) + ${layout.width}px)` }}>
        <div className="tl-head" style={{ height: AXIS_H }}>
        <div className="tl-corner">
          {view === 'threads' ? (
            <button
              type="button"
              className={`tl-corner-btn${selection?.type === 'overview' ? ' on' : ''}`}
              title="项目概览：每条研究线的进展和里程碑"
              onClick={(e) => {
                e.stopPropagation();
                onSelect(selection?.type === 'overview' ? null : { type: 'overview' });
              }}
            >
              {layout.lanes.filter((l) => l.threadId !== undefined).length} 条研究线 · 概览
            </button>
          ) : (
            `${layout.lanes.length} 个会话`
          )}
        </div>
        <div className="tl-axis" style={{ width: layout.width }}>
          {align === 'compact' ? (
            <span className="tl-axis-hint">每条{view === 'threads' ? '研究线' : '会话'}各自从左边排起，只看先后，不对齐日期</span>
          ) : null}
          {layout.days.map((day) => (
            <div key={`${day.key}:${day.x}`} className="tl-day" style={{ left: day.x, width: day.width }}>
              <span>{fmtDayKey(day.key)}</span>
            </div>
          ))}
          {layout.ticks.map((tick) => (
            <span key={tick.x} className="tl-tick" style={{ left: tick.x }}>
              {tick.label}
            </span>
          ))}
        </div>
        </div>
        <div className="tl-body" style={{ height: layout.height }}>
        <div className="tl-labels" ref={labels}>
          {layout.lanes.map((lane) => (
            <button
              key={lane.id}
              type="button"
              className={`tl-lane${selectedLane === lane.id ? ' on' : ''}`}
              style={{ top: lane.y, height: lane.height }}
              title={lane.note}
              onClick={(e) => {
                e.stopPropagation();
                onSelect({ type: 'lane', lane });
              }}
            >
              <span className="tl-lane-title">{lane.title}</span>
              <span className="tl-lane-sub">{lane.subtitle}</span>
              {lane.rows.length > 1
                ? lane.rows.slice(1).map((row) => (
                    <span key={row.id} className="tl-row-label" style={{ top: row.y - lane.y + row.height / 2 - 9 }}>
                      <Icon name="branch" size={12} />
                      {row.label}
                    </span>
                  ))
                : null}
            </button>
          ))}
        </div>
        <div className="tl-canvas" style={{ width: layout.width }}>
          {layout.lanes.map((lane) => (
            <div key={lane.id} className="tl-lane-line" style={{ top: lane.y + lane.height - 1 }} />
          ))}
          {layout.days.slice(1).map((day) => (
            <div key={`d:${day.x}`} className="tl-day-line" style={{ left: day.x }} />
          ))}
          <svg className="tl-edges" width={layout.width} height={layout.height}>
            <defs>
              <marker id="tl-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" fill="var(--edge)" />
              </marker>
              <marker id="tl-arrow-on" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" fill="var(--ink)" />
              </marker>
            </defs>
            {layout.edges.map((edge) => {
              const on = selectedEvent !== null && (edge.from === selectedEvent || edge.to === selectedEvent);
              if (align === 'compact' && edge.cross && !on) return null;
              const arrow = edge.kind !== 'next';
              return (
                <path
                  key={edge.id}
                  className={`tl-edge ${edge.kind}${on ? ' on' : ''}`}
                  d={edge.path}
                  markerEnd={arrow ? (on ? 'url(#tl-arrow-on)' : 'url(#tl-arrow)') : undefined}
                />
              );
            })}
          </svg>
          {layout.nodes.map((node) =>
            align === 'compact' ? (
              <Card
                key={node.event.id}
                node={node}
                cross={crossLinked.has(node.event.id)}
                fresh={fresh.has(node.event.id)}
                dim={matches !== null && !matches.has(node.event.id)}
                more={hidden.get(node.event.id) ?? 0}
                open={opened.has(node.event.id)}
                revealed={revealed.has(node.event.id)}
                onToggle={onToggle}
                on={node.event.id === selectedEvent}
                onSelect={onSelect}
                onHover={setHovered}
              />
            ) : (
              <Dot
                key={node.event.id}
                node={node}
                fresh={fresh.has(node.event.id)}
                dim={matches !== null && !matches.has(node.event.id)}
                on={node.event.id === selectedEvent}
                onSelect={onSelect}
                onHover={setHovered}
              />
            ),
          )}
          {hoveredNode ? <Tip node={hoveredNode} align={align} session={sessions.get(hoveredNode.event.session_id)} /> : null}
        </div>
        </div>
      </div>
    </div>
  );
}

interface NodeProps {
  node: PlacedNode;
  on: boolean;
  cross?: boolean;
  fresh?: boolean;
  dim?: boolean;
  more?: number;
  open?: boolean;
  revealed?: boolean;
  onToggle?: (eventId: number) => void;
  onSelect: (selection: Selection) => void;
  onHover: (id: number | null) => void;
}

function handlers({ node, onSelect, onHover }: NodeProps) {
  return {
    'data-id': node.event.id,
    onClick: (e: MouseEvent) => {
      e.stopPropagation();
      onSelect({ type: 'event', id: node.event.id });
    },
    onPointerEnter: () => onHover(node.event.id),
    onPointerLeave: () => onHover(null),
    onFocus: () => onHover(node.event.id),
    onBlur: () => onHover(null),
  };
}

function Card(props: NodeProps) {
  const { node, on, cross, fresh, dim, more = 0, open, revealed, onToggle } = props;
  const { event } = node;
  const classes = [
    'tl-card',
    `k-${event.kind}`,
    on ? 'on' : '',
    dim ? 'dim' : '',
    event.importance === 3 ? 'milestone' : '',
    event.outcome === 'failed' ? 'failed' : '',
    event.importance === 1 ? 'minor' : '',
    revealed ? 'revealed' : '',
  ];
  return (
    <button
      type="button"
      className={classes.filter(Boolean).join(' ')}
      style={{ left: node.x, top: node.y, width: CARD_W, height: node.h }}
      aria-label={`${eventLabel(event)}：${event.title}`}
      {...handlers(props)}
    >
      <span className="tl-card-meta">
        <KindGlyph kind={event.kind} outcome={event.outcome} />
        <span>{KIND_LABEL[event.kind]}</span>
        {event.outcome && event.outcome !== 'open' ? (
          <span className={`tl-outcome ${event.outcome}`} title={event.outcome === 'failed' ? '失败的尝试' : '成功的尝试'}>
            {event.outcome === 'failed' ? '✗' : '✓'}
          </span>
        ) : null}
        {fresh ? <span className="tl-new">新</span> : null}
        {more || open ? (
          // 卡片本身是按钮，里面不能再套可聚焦的按钮；键盘用户用面板里的“在时间线上展开”
          <span
            className={`tl-more${open ? ' open' : ''}`}
            title={open ? '收起这一段' : `展开这一段：还有 ${more} 个事件`}
            onClick={(e) => {
              e.stopPropagation();
              onToggle?.(event.id);
            }}
          >
            {open ? '−' : `+${more}`}
          </span>
        ) : null}
        <span className="tl-card-time">{fmtTime(event.ts)}</span>
        {cross ? (
          <span className="tl-card-flag" title="和别的泳道有分出或汇入关系，选中后显示连线">
            <Icon name="branch" size={11} />
          </span>
        ) : null}
        {event.edited ? (
          <span className="tl-card-edited" title="手动改过">
            <Icon name="edit" size={11} />
          </span>
        ) : null}
      </span>
      <span className="tl-card-title" style={{ WebkitLineClamp: node.lines }}>
        {event.title}
      </span>
    </button>
  );
}

function Dot(props: NodeProps) {
  const { node, on, fresh, dim } = props;
  const { event } = node;
  const milestone = event.importance === 3;
  return (
    <button
      type="button"
      className={`tl-dot k-${event.kind}${on ? ' on' : ''}${fresh ? ' fresh' : ''}${dim ? ' dim' : ''}${milestone ? ' milestone' : ''}`}
      style={{ left: node.x - DOT_HIT / 2, top: node.y - DOT_HIT / 2 }}
      aria-label={`${eventLabel(event)}：${event.title}`}
      {...handlers(props)}
    >
      <span className="tl-dot-mark">
        <KindGlyph kind={event.kind} outcome={event.outcome} size={milestone ? 14 : 12} ring />
      </span>
      {node.labelWidth > 0 ? (
        <span className="tl-dot-label" style={{ maxWidth: node.labelWidth - 6 }}>
          {event.title}
        </span>
      ) : null}
    </button>
  );
}

function Tip({ node, align, session }: { node: PlacedNode; align: Align; session?: Session }) {
  const { event } = node;
  const centerX = align === 'compact' ? node.x + CARD_W / 2 : node.x;
  const top = align === 'compact' ? node.y : node.y - DOT_HIT / 2;
  const bottom = align === 'compact' ? node.y + node.h : node.y + DOT_HIT / 2;
  const below = top < 140;
  return (
    <div
      className={`tl-tip${below ? ' below' : ''}`}
      style={{ left: Math.max(150, centerX), top: below ? bottom + 8 : top - 8 }}
      role="tooltip"
    >
      <div className="tl-tip-meta">
        <KindGlyph kind={event.kind} outcome={event.outcome} />
        <span>{eventLabel(event)}</span>
        <span>·</span>
        <span>{fmtTime(event.ts)}</span>
      </div>
      <div className="tl-tip-title">{event.title}</div>
      {event.summary ? <div className="tl-tip-summary">{event.summary}</div> : null}
      {session ? (
        <div className="tl-tip-session">
          {TOOL_LABEL[session.tool]} ·「{session.title ?? '无标题'}」
        </div>
      ) : null}
    </div>
  );
}
