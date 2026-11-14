/* Calendar page — month / week / day views, full CRUD, event-detail rail.
 *
 * Data sources:
 *   * GET    /api/calendar/events            — the six weeks around the
 *                                              month on screen (the UI
 *                                              re-windows per view)
 *   * POST   /api/calendar/events            — create (always source='local')
 *   * PATCH  /api/calendar/events/{id}       — partial update
 *   * DELETE /api/calendar/events/{id}       — delete
 *   * GET    /api/calendar/sources           — the calendars shown from
 *                                              links and phones (1.3.0)
 *   * /ws/state · `calendar.event.changed`   — refresh on remote change
 *
 * An event from a linked calendar or a phone (read_only) wears its
 * calendar's colour and name and can't be edited or deleted here: the
 * house only shows it ("from Work. change it there"). A link that can't
 * refresh says so above the grid, with the age of the copy shown.
 */

/* ---- Date helpers ----------------------------------------------- */
const startOfDay   = (d) => { const x = new Date(d); x.setHours(0,0,0,0); return x; };
const startOfMonth = (d) => { const x = startOfDay(d); x.setDate(1); return x; };
const startOfWeek  = (d) => { const x = startOfDay(d); x.setDate(x.getDate() - x.getDay()); return x; };
const addDays      = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const addMonths    = (d, n) => { const x = new Date(d); x.setMonth(x.getMonth() + n); return x; };
const sameDay      = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
const dateKey      = (d) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;

const fmtDayLabel  = (d) => d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toLowerCase();
const fmtDateLong  = (d) => d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
const fmtMonthYr   = (d) => d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' }).toLowerCase();
const fmtClock     = (iso) => {
  const d = new Date(iso);
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }).toLowerCase().replace(' ','');
};
const fmtRange     = (a, b) => `${fmtClock(a)} – ${fmtClock(b)}`;
const fmtIsoLocal  = (iso) => {
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2,'0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const localToIso   = (s) => new Date(s).toISOString();

/* ---- Where an event comes from ---------------------------------- */
// Household events are amber; anything from outside (a link, a phone, the
// old synced kind) is the slate "from elsewhere" style, its stripe and dot
// in the calendar's own colour.
const calIsExt   = (e) => !!(e && (e.read_only || e.source === 'google'));
const calSrcCls  = (e) => (calIsExt(e) ? 'src-ext' : 'src-local');
const calStyle   = (e) => (e && e.calendar_color ? { '--cal-c': e.calendar_color } : undefined);
const calWhen    = (e) => (e.all_day ? 'all day' : fmtClock(e.starts_at));
const calFromNote = (e) => {
  if (!e || !e.read_only) return null;
  const name = e.calendar_name || 'another calendar';
  if (e.calendar_kind === 'phone') {
    return `from ${name}${e.owner_name ? ` on ${e.owner_name}'s phone` : ''}. change it on that phone`;
  }
  return `from ${name}. change it there; Domovoi only shows it`;
};
// The days an event covers: an all-day one every day to its end (at most a
// month), a timed one the day it starts.
const calDaysOf  = (e) => {
  const s = startOfDay(new Date(e.starts_at));
  if (!e.all_day || !e.ends_at) return [s];
  const out = [];
  const end = new Date(e.ends_at);
  for (let d = s; d < end && out.length < 31; d = addDays(d, 1)) out.push(d);
  return out.length ? out : [s];
};

/* ---- Top bar (view toggle + nav + new event) -------------------- */
const ViewToggle = ({ value, onChange }) => (
  <div className="cal-view-toggle" role="tablist">
    {['month','week','day'].map(v => (
      <button key={v} role="tab" aria-selected={value === v}
              className={`cal-view-tab ${value === v ? 'on' : ''}`}
              onClick={() => onChange(v)}>{v}</button>
    ))}
  </div>
);

const CalTopBar = ({ view, setView, anchor, setAnchor, onNew }) => {
  const goPrev  = () => setAnchor(view === 'month' ? addMonths(anchor, -1) : addDays(anchor, view === 'week' ? -7 : -1));
  const goNext  = () => setAnchor(view === 'month' ? addMonths(anchor,  1) : addDays(anchor, view === 'week' ?  7 :  1));
  const goToday = () => setAnchor(new Date());

  let label;
  if (view === 'month') label = fmtMonthYr(anchor);
  else if (view === 'week') {
    const ws = startOfWeek(anchor); const we = addDays(ws, 6);
    const sameMonth = ws.getMonth() === we.getMonth();
    label = sameMonth
      ? `${ws.toLocaleDateString('en-US',{month:'short'}).toLowerCase()} ${ws.getDate()} – ${we.getDate()}, ${we.getFullYear()}`
      : `${ws.toLocaleDateString('en-US',{month:'short',day:'numeric'}).toLowerCase()} – ${we.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}).toLowerCase()}`;
  }
  else label = anchor.toLocaleDateString('en-US',{ weekday:'long', month:'long', day:'numeric' }).toLowerCase();

  return (
    <div className="cal-topbar">
      <ViewToggle value={view} onChange={setView}/>
      <Button onClick={goToday}>today</Button>
      <div className="cal-nav">
        <IconButton name="chevron-left" onClick={goPrev} aria-label="previous"/>
        <div className="cal-nav-label">{label}</div>
        <IconButton name="chevron-right" onClick={goNext} aria-label="next"/>
      </div>
      <div style={{ flex: 1 }}/>
      <Button variant="primary" icon="plus" onClick={() => onNew()}>new event</Button>
    </div>
  );
};

/* ---- Event chip (used in month grid) ---------------------------- */
const EventChip = ({ event, selected, onClick }) => (
  <button
    type="button"
    className={`cal-chip ${calSrcCls(event)} ${selected ? 'sel' : ''}`}
    style={calStyle(event)}
    onClick={(e) => { e.stopPropagation(); onClick(event.id); }}
    title={`${event.title} · ${event.all_day ? 'all day' : fmtRange(event.starts_at, event.ends_at)}${event.calendar_name ? ` · ${event.calendar_name}` : ''}`}
  >
    <span className="dot"/>
    <span className="t mono">{calWhen(event)}</span>
    <span className="ti">{event.title}</span>
  </button>
);

/* ---- Month grid ------------------------------------------------- */
const MonthGrid = ({ anchor, events, selectedId, onSelect, onPickDay }) => {
  const monthStart = startOfMonth(anchor);
  const gridStart  = startOfWeek(monthStart);
  const days = Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));
  const today = new Date();
  const eventsByDay = React.useMemo(() => {
    const m = {};
    for (const e of events) {
      for (const d of calDaysOf(e)) {
        const k = dateKey(d);
        (m[k] = m[k] || []).push(e);
      }
    }
    // All-day first, then by start.
    for (const k of Object.keys(m)) {
      m[k].sort((a, b) => (b.all_day - a.all_day) || (Date.parse(a.starts_at) - Date.parse(b.starts_at)));
    }
    return m;
  }, [events]);

  return (
    <div className="cal-month">
      <div className="cal-month-head">
        {['sun','mon','tue','wed','thu','fri','sat'].map(d => <div key={d}>{d}</div>)}
      </div>
      <div className="cal-month-grid">
        {days.map((d, i) => {
          const k = dateKey(d);
          const inMonth = d.getMonth() === anchor.getMonth();
          const isToday = sameDay(d, today);
          const dayEvents = eventsByDay[k] || [];
          const visible = dayEvents.slice(0, 3);
          const overflow = dayEvents.length - visible.length;
          return (
            <div key={i}
                 className={`cal-cell ${inMonth ? '' : 'out'} ${isToday ? 'today' : ''}`}
                 onClick={() => onPickDay(d)}>
              <div className="cal-cell-num">
                <span className={isToday ? 'today-pill' : ''}>{d.getDate()}</span>
              </div>
              <div className="cal-cell-events">
                {visible.map(e => (
                  <EventChip key={e.id} event={e} selected={selectedId === e.id} onClick={onSelect}/>
                ))}
                {overflow > 0 && (
                  <button type="button" className="cal-cell-more"
                          onClick={(ev) => { ev.stopPropagation(); onPickDay(d); }}>
                    +{overflow} more
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ---- Time-grid (week + day shared) ------------------------------ */
const HOURS = Array.from({ length: 16 }, (_, i) => i + 6); // 6am - 10pm

const eventLayout = (e) => {
  const s = new Date(e.starts_at), en = new Date(e.ends_at || e.starts_at);
  const startMin = (s.getHours() - HOURS[0]) * 60 + s.getMinutes();
  const dur = Math.max(20, (en - s) / 60000);
  return { top: startMin, height: dur };
};

const TimeGridEvent = ({ event, selected, onClick }) => {
  const { top, height } = eventLayout(event);
  return (
    <button
      type="button"
      className={`cal-tg-event ${calSrcCls(event)} ${selected ? 'sel' : ''}`}
      style={{ top, height, ...(calStyle(event) || {}) }}
      onClick={(e) => { e.stopPropagation(); onClick(event.id); }}
    >
      <div className="ti">{event.title}</div>
      <div className="ts mono">{fmtRange(event.starts_at, event.ends_at)}</div>
      {event.location && height > 60 && <div className="loc">{event.location}</div>}
    </button>
  );
};

const TimeColumn = ({ day, events, selectedId, onSelect }) => {
  // All-day events sit in the header row (CalAllDay), not on the hours.
  const dayEvents = events.filter(e => !e.all_day && sameDay(new Date(e.starts_at), day));
  const today = new Date();
  const isToday = sameDay(day, today);
  return (
    <div className={`cal-tg-col ${isToday ? 'today' : ''}`}>
      {HOURS.map(h => <div key={h} className="cal-tg-hr"/>)}
      {isToday && (() => {
        const m = (today.getHours() - HOURS[0]) * 60 + today.getMinutes();
        if (m < 0 || m > HOURS.length * 60) return null;
        return <div className="cal-tg-now" style={{ top: m }}><span className="dot"/></div>;
      })()}
      {dayEvents.map(e => (
        <TimeGridEvent key={e.id} event={e} selected={selectedId === e.id} onClick={onSelect}/>
      ))}
    </div>
  );
};

const HourLabels = () => (
  <div className="cal-tg-hours">
    {HOURS.map(h => (
      <div key={h} className="cal-tg-hr-lab mono">
        {((h + 11) % 12) + 1}{h < 12 ? 'am' : 'pm'}
      </div>
    ))}
  </div>
);

/* All-day events of one day, under its date in the week and day headers. */
const CalAllDay = ({ day, events, selectedId, onSelect }) => {
  const list = events.filter(e => e.all_day && calDaysOf(e).some(d => sameDay(d, day)));
  if (!list.length) return null;
  return (
    <div className="cal-tg-allday">
      {list.map(e => <EventChip key={e.id} event={e} selected={selectedId === e.id} onClick={onSelect}/>)}
    </div>
  );
};

/* ---- Week view -------------------------------------------------- */
const WeekGrid = ({ anchor, events, selectedId, onSelect }) => {
  const ws = startOfWeek(anchor);
  const days = Array.from({ length: 7 }, (_, i) => addDays(ws, i));
  const today = new Date();
  return (
    <div className="cal-tg">
      <div className="cal-tg-headrow">
        <div className="cal-tg-corner"/>
        {days.map((d, i) => {
          const isToday = sameDay(d, today);
          return (
            <div key={i} className={`cal-tg-headcell ${isToday ? 'today' : ''}`}>
              <div className="d">{d.toLocaleDateString('en-US',{ weekday:'short' }).toLowerCase()}</div>
              <div className={`n ${isToday ? 'today-pill' : ''}`}>{d.getDate()}</div>
              <CalAllDay day={d} events={events} selectedId={selectedId} onSelect={onSelect}/>
            </div>
          );
        })}
      </div>
      <div className="cal-tg-body">
        <HourLabels/>
        <div className="cal-tg-cols week">
          {days.map((d, i) => (
            <TimeColumn key={i} day={d} events={events} selectedId={selectedId} onSelect={onSelect}/>
          ))}
        </div>
      </div>
    </div>
  );
};

/* ---- Day view --------------------------------------------------- */
const DayGrid = ({ anchor, events, selectedId, onSelect }) => {
  const today = new Date();
  return (
    <div className="cal-tg">
      <div className="cal-tg-headrow day">
        <div className="cal-tg-corner"/>
        <div className={`cal-tg-headcell big ${sameDay(anchor, today) ? 'today' : ''}`}>
          <div className="d">{anchor.toLocaleDateString('en-US',{ weekday:'long' }).toLowerCase()}</div>
          <div className={`n ${sameDay(anchor, today) ? 'today-pill' : ''}`}>{anchor.getDate()}</div>
          <CalAllDay day={anchor} events={events} selectedId={selectedId} onSelect={onSelect}/>
        </div>
      </div>
      <div className="cal-tg-body">
        <HourLabels/>
        <div className="cal-tg-cols day">
          <TimeColumn day={anchor} events={events} selectedId={selectedId} onSelect={onSelect}/>
        </div>
      </div>
    </div>
  );
};

/* ---- Mobile list view (used at narrow widths) ------------------- */
const MobileList = ({ events, selectedId, onSelect, anchor }) => {
  const today = new Date();
  const upcoming = events
    .filter(e => new Date(e.ends_at || e.starts_at) >= startOfDay(anchor))
    .sort((a,b) => a.starts_at.localeCompare(b.starts_at))
    .slice(0, 25);
  if (upcoming.length === 0)
    return <CalEmpty title="nothing on the calendar" sub="tap + to add an event"/>;
  const grouped = {};
  for (const e of upcoming) {
    const k = dateKey(new Date(e.starts_at));
    (grouped[k] = grouped[k] || []).push(e);
  }
  return (
    <div className="cal-mlist">
      {Object.entries(grouped).map(([k, list]) => {
        const d = new Date(k + 'T12:00:00');
        return (
          <div key={k} className="cal-mlist-day">
            <div className="cal-mlist-head">
              <div className="lab">{sameDay(d, today) ? 'today' : fmtDayLabel(d)}</div>
              <div className="mono">{k}</div>
            </div>
            {list.map(e => (
              <button key={e.id} className={`cal-mlist-row ${selectedId === e.id ? 'sel' : ''}`}
                      onClick={() => onSelect(e.id)}>
                <div className="time mono">
                  <div>{calWhen(e)}</div>
                  {e.ends_at && !e.all_day && <div className="end">{fmtClock(e.ends_at)}</div>}
                </div>
                <div className="body">
                  <div className="ti">{e.title}</div>
                  <div className="meta">
                    {e.location && <span><Icon name="map-pin" size={11}/> {e.location}</span>}
                  </div>
                </div>
                <CalSourceTag event={e}/>
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
};

/* The calendar an event comes from: a dot in its colour and its name;
 * "household" for the house's own. */
const CalSourceTag = ({ event }) => (
  <span className={`cal-src-tag ${calSrcCls(event)}`} style={calStyle(event)}>
    <span className="dot"/>{calIsExt(event) ? (event.calendar_name || 'synced') : 'household'}
  </span>
);

/* ---- Empty state ------------------------------------------------ */
const CalEmpty = ({ title, sub }) => (
  <div className="cal-empty">
    <SleepingDomovoi size={104}/>
    <div className="t">{title}</div>
    <div className="s">{sub}</div>
  </div>
);

/* ---- Event detail rail ------------------------------------------ */
const EventDetail = ({ event, onClose, onEdit, onDelete }) => {
  if (!event) return null;
  const ext = calIsExt(event);
  const note = calFromNote(event);
  return (
    <aside className="cal-detail">
      <div className="cal-detail-head">
        <div className={`src-stripe ${calSrcCls(event)}`} style={calStyle(event)}/>
        <div style={{ flex: 1 }}>
          <div className="ttl">{event.title}</div>
          <div className="when mono">{fmtDateLong(new Date(event.starts_at)).toLowerCase()}</div>
          {event.all_day
            ? <div className="when mono">all day</div>
            : event.ends_at && <div className="when mono">{fmtRange(event.starts_at, event.ends_at)}</div>}
        </div>
        <IconButton name="x" onClick={onClose} aria-label="close"/>
      </div>
      <div className="cal-detail-body">
        {event.location && (
          <div className="row">
            <Icon name="map-pin" size={14}/>
            <div>{event.location}</div>
          </div>
        )}
        {event.description && (
          <div className="row">
            <Icon name="align-left" size={14}/>
            <div className="desc">{event.description}</div>
          </div>
        )}
        <div className="row">
          <Icon name={event.calendar_kind === 'phone' ? 'smartphone' : ext ? 'link' : 'pencil'} size={14}/>
          <div>
            <CalSourceTag event={event}/>
            {event.last_synced_at && (
              <span className="mono" style={{ marginLeft: 8, fontSize: 11, color: 'var(--fg-faint)' }}>
                synced {relTime(event.last_synced_at)}
              </span>
            )}
          </div>
        </div>
        <div className="row">
          <Icon name="hash" size={14}/>
          <div className="mono" style={{ fontSize: 11, color: 'var(--fg-faint)' }}>
            event #{event.id}
          </div>
        </div>
      </div>
      {note ? (
        <div className="cal-detail-foot cal-detail-ro">{note}</div>
      ) : (
        <div className="cal-detail-foot">
          <Button icon="pencil" onClick={() => onEdit(event)}>edit</Button>
          <Button icon="trash-2" onClick={() => onDelete(event)} style={{ color: 'var(--err-ink)' }}>delete</Button>
        </div>
      )}
    </aside>
  );
};

/* ---- New / Edit event modal ------------------------------------- */
const blankDraft = (anchor) => {
  const start = new Date(anchor);
  start.setHours(Math.max(9, start.getHours()), 0, 0, 0);
  const end = new Date(start.getTime() + 60 * 60 * 1000);
  return {
    id: null, title: '', starts_at: start.toISOString(), ends_at: end.toISOString(),
    location: '', description: '', source: 'local', last_synced_at: null
  };
};

const EventModal = ({ open, draft, onClose, onSave }) => {
  const [d, setD] = React.useState(draft);
  const [errs, setErrs] = React.useState({});
  React.useEffect(() => { if (open) { setD(draft); setErrs({}); } }, [open, draft]);
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;

  const set = (k, v) => setD(prev => ({ ...prev, [k]: v }));
  const setStart = (val) => {
    const startIso = localToIso(val);
    const oldDur = new Date(d.ends_at) - new Date(d.starts_at);
    const newEnd = new Date(new Date(startIso).getTime() + (oldDur > 0 ? oldDur : 3600000));
    setD(prev => ({ ...prev, starts_at: startIso, ends_at: newEnd.toISOString() }));
  };
  const setEnd = (val) => set('ends_at', localToIso(val));

  const submit = () => {
    const e = {};
    if (!d.title.trim()) e.title = 'title is required';
    if (!d.starts_at) e.starts_at = 'start is required';
    if (d.ends_at && new Date(d.ends_at) <= new Date(d.starts_at)) e.ends_at = 'end must be after start';
    setErrs(e);
    if (Object.keys(e).length === 0) onSave({ ...d, title: d.title.trim() });
  };

  const isEdit = d.id != null;
  return (
    <div className="cal-modal-bg" onClick={onClose}>
      <div className="cal-modal" onClick={(e) => e.stopPropagation()}>
        <div className="cal-modal-head">
          <div className="ttl">{isEdit ? 'edit event' : 'new event'}</div>
          <IconButton name="x" onClick={onClose} aria-label="close"/>
        </div>
        <div className="cal-modal-body">
          <div className="field">
            <label>title</label>
            <input className="cal-inp" autoFocus value={d.title}
                   placeholder="e.g. dinner with mom"
                   onChange={(e) => set('title', e.target.value)}/>
            {errs.title && <div className="err">{errs.title}</div>}
          </div>
          <div className="field-row">
            <div className="field">
              <label>starts at</label>
              <input className="cal-inp" type="datetime-local" value={fmtIsoLocal(d.starts_at)}
                     onChange={(e) => setStart(e.target.value)}/>
            </div>
            <div className="field">
              <label>ends at</label>
              <input className="cal-inp" type="datetime-local" value={fmtIsoLocal(d.ends_at)}
                     onChange={(e) => setEnd(e.target.value)}/>
              {errs.ends_at && <div className="err">{errs.ends_at}</div>}
            </div>
          </div>
          <div className="field">
            <label>location</label>
            <input className="cal-inp" value={d.location || ''}
                   placeholder="optional"
                   onChange={(e) => set('location', e.target.value)}/>
          </div>
          <div className="field">
            <label>description</label>
            <textarea className="cal-inp" rows={3} value={d.description || ''}
                      placeholder="optional notes"
                      onChange={(e) => set('description', e.target.value)}/>
          </div>
        </div>
        <div className="cal-modal-foot">
          <Button onClick={onClose}>cancel</Button>
          <Button variant="primary" onClick={submit}>{isEdit ? 'save' : 'create event'}</Button>
        </div>
      </div>
    </div>
  );
};

/* ---- Page ------------------------------------------------------- */
// The view toggle's views, by address: #calendar/month|week|day
// (useRouteTab, components.jsx). The day a view shows is not in it.
const CAL_VIEWS = [{ id: 'month' }, { id: 'week' }, { id: 'day' }];

const CalendarPage = () => {
  const [view, setView]         = useRouteTab('calendar', CAL_VIEWS, 'month');
  const [anchor, setAnchor]     = React.useState(() => new Date());
  const [selectedId, setSelId]  = React.useState(null);
  const [modalOpen, setModalOp] = React.useState(false);
  const [draft, setDraft]       = React.useState(() => blankDraft(new Date()));
  const [fire, toastNode]       = useToast();

  // The six weeks of the month grid around the anchor: every view (and the
  // phone list, from the anchor on) falls inside it. Linked calendars can
  // hold thousands of events, so the page asks for its window, not all.
  const calYear = anchor.getFullYear();
  const calMonth = anchor.getMonth();
  const calPath = React.useMemo(() => {
    const gs = startOfWeek(new Date(calYear, calMonth, 1));
    const ge = addDays(gs, 42);
    return `/api/calendar/events?start=${encodeURIComponent(gs.toISOString())}`
      + `&end=${encodeURIComponent(ge.toISOString())}&limit=2000`;
  }, [calYear, calMonth]);
  const { items: events, refresh: refreshEvents } =
    useApiList(calPath, { eventTypes: ['calendar.events.changed'] });
  const { items: sources } =
    useApiList('/api/calendar/sources', { eventTypes: ['calendar.events.changed'], quiet: true });

  const visible = React.useMemo(() => {
    if (view === 'month') {
      const ms = startOfMonth(anchor);
      const gs = startOfWeek(ms);
      const ge = addDays(gs, 42);
      return events.filter(e => calDaysOf(e).some(d => d >= gs && d < ge));
    }
    if (view === 'week') {
      const ws = startOfWeek(anchor);
      const we = addDays(ws, 7);
      return events.filter(e => calDaysOf(e).some(d => d >= ws && d < we));
    }
    return events.filter(e => calDaysOf(e).some(d => sameDay(d, anchor)));
  }, [view, anchor, events]);

  const selected = events.find(e => e.id === selectedId);

  const openNew = (forDay) => {
    setDraft(blankDraft(forDay || anchor));
    setSelId(null);
    setModalOp(true);
  };
  const openEdit = (e) => { if (e.read_only) return; setDraft({ ...e }); setModalOp(true); };

  const save = async (e) => {
    try {
      const body = {
        title: e.title,
        starts_at: e.starts_at,
        ends_at: e.ends_at || null,
        location: e.location || null,
        description: e.description || null,
      };
      if (e.id == null) {
        const created = await apiPost('/api/calendar/events', body);
        if (created?.id) setSelId(created.id);
        fire('event created');
      } else {
        await apiPatch(`/api/calendar/events/${e.id}`, body);
        setSelId(e.id);
        fire('event updated');
      }
      setModalOp(false);
      refreshEvents();
    } catch (err) {
      reportMutationFailure(fire, 'save', err, { kept: true });
    }
  };

  const del = async (e) => {
    try {
      await apiDelete(`/api/calendar/events/${e.id}`);
      if (selectedId === e.id) setSelId(null);
      fire(`deleted "${e.title}"`);
      refreshEvents();
    } catch (err) {
      // The login modal owns a 401 (data.js) — no raw toast behind it (F-006).
      reportMutationFailure(fire, 'delete', err);
    }
  };

  const pickDay = (d) => {
    if (view === 'month' || view === 'week') { setAnchor(d); setView('day'); }
  };

  const calBody = view === 'month'
    ? <MonthGrid anchor={anchor} events={visible} selectedId={selectedId} onSelect={setSelId} onPickDay={pickDay}/>
    : view === 'week'
      ? <WeekGrid anchor={anchor} events={visible} selectedId={selectedId} onSelect={setSelId}/>
      : <DayGrid  anchor={anchor} events={visible} selectedId={selectedId} onSelect={setSelId}/>;

  const isEmpty = visible.length === 0;

  return (
    <div className="page cal-page">
      <PageHeader
        title="Calendar"
        sub={<>
          {`${visible.length} ${visible.length === 1 ? 'event' : 'events'}`}
          {' · '}<a href="#settings/calendars" className="home-link">
            {sources && sources.length
              ? `${sources.length} other ${sources.length === 1 ? 'calendar' : 'calendars'}`
              : 'show another calendar here'}
          </a>
        </>}
      />

      <CalTopBar view={view} setView={setView} anchor={anchor} setAnchor={setAnchor} onNew={openNew}/>
      <CalSourceNotices sources={sources}/>

      {/* Mobile list (only visible at narrow widths via CSS) */}
      <div className="cal-mobile">
        <MobileList events={events} selectedId={selectedId} onSelect={setSelId} anchor={anchor}/>
      </div>

      {/* Desktop layout: calendar + optional rail */}
      <div className={`cal-stage ${selected ? 'with-rail' : ''}`}>
        <div className="cal-canvas">
          {isEmpty
            ? <CalEmpty title="nothing in this range" sub={`tap + to add an event for ${view === 'day' ? fmtDayLabel(anchor) : view}`}/>
            : calBody}
        </div>
        {selected && (
          <EventDetail event={selected}
                       onClose={() => setSelId(null)}
                       onEdit={openEdit}
                       onDelete={del}/>
        )}
      </div>

      <EventModal open={modalOpen} draft={draft}
                  onClose={() => setModalOp(false)} onSave={save}/>
      {toastNode}
    </div>
  );
};

/* ---- Settings → Household → Calendars (1.3.0) ------------------------
 * The calendars the house shows besides its own: private ICS links an
 * admin pasted (Google, Outlook, iCloud), and the calendars household
 * phones share from the Domovoi app. Read-only, both: nothing is ever
 * written back, and nobody signs in to any calendar company.
 *
 *   GET    /api/calendar/sources               (household)
 *   POST   /api/calendar/sources               (admin) add a link
 *   PATCH  /api/calendar/sources/{id}          (admin) name, colour, whose, new link
 *   DELETE /api/calendar/sources/{id}          (admin) a link only
 *   POST   /api/calendar/sources/{id}/refresh  (admin) "refresh now"
 *
 * The link is a secret (anyone holding it can read the calendar): the
 * server keeps it and only ever answers with its host. */
const CAL_COLORS = ['#5b7a99', '#3f8f6b', '#7a5fb0', '#b0566b', '#2f86a6', '#8a7a3a', '#5f6b7a'];

const calSourceLine = (s) => {
  const bits = [];
  if (s.kind === 'link') {
    bits.push(s.url_display || 'link');
    if (s.owner_name) bits.push(s.owner_name);
  } else {
    bits.push(`on ${s.owner_name || s.device_name || 'a phone'}`);
    if (s.account_name) bits.push(s.account_name);
  }
  bits.push(`${s.event_count} ${s.event_count === 1 ? 'event' : 'events'}`);
  if (s.last_refreshed_at) bits.push(`${s.kind === 'phone' ? 'updated' : 'read'} ${relTime(s.last_refreshed_at)}`);
  else if (s.kind === 'link') bits.push('not read yet');
  return bits.join(' · ');
};

const CalColorPicker = ({ value, onChange }) => (
  <div className="calsrc-colors" role="group" aria-label="colour">
    {CAL_COLORS.map((c) => (
      <button key={c} type="button" className="calsrc-color" aria-pressed={value === c}
              aria-label={`colour ${c}`} style={{ '--cal-c': c }} onClick={() => onChange(c)}/>
    ))}
  </div>
);

const CalPersonSelect = ({ people, value, onChange }) => (
  <select className="cal-inp" value={value == null ? '' : String(value)}
          onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
    <option value="">the household</option>
    {(people || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
  </select>
);

const CalLinkForm = ({ people, initial, onDone, onCancel, fire }) => {
  const editing = !!initial;
  const [url, setUrl] = React.useState('');
  const [name, setName] = React.useState(initial ? initial.name : '');
  const [color, setColor] = React.useState(initial ? initial.color : CAL_COLORS[0]);
  const [person, setPerson] = React.useState(initial ? initial.person_id : null);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState(null);
  const submit = async (ev) => {
    ev.preventDefault();
    if (!editing && !url.trim()) { setErr('paste the calendar’s link'); return; }
    if (!name.trim()) { setErr('give it a name'); return; }
    setBusy(true); setErr(null);
    try {
      let res;
      if (editing) {
        const body = { name: name.trim(), color, person_id: person };
        if (url.trim()) body.url = url.trim();
        res = await apiPatch(`/api/calendar/sources/${initial.id}`, body);
        fire(`${name.trim()} saved`);
      } else {
        res = await apiPost('/api/calendar/sources',
                            { url: url.trim(), name: name.trim(), color, person_id: person });
        const s = res && res.source;
        if (res && res.ok) fire(`${name.trim()} added: ${s ? s.event_count : 0} events`, { tone: 'ok' });
        else fire(`${name.trim()} added, but it can’t be read yet: ${(s && s.last_error_words) || 'see below'}`,
                  { tone: 'warn' });
      }
      onDone(res);
    } catch (e) {
      const status = e && e.status;
      const detail = (status === 409 || status === 422) && e.detail && typeof e.detail.detail === 'string'
        ? e.detail.detail : null;
      if (detail) setErr(detail);
      else reportMutationFailure(fire, editing ? 'save' : 'add', e, { kept: true });
    } finally { setBusy(false); }
  };
  return (
    <form className="calsrc-form" onSubmit={submit}>
      <div>
        <label htmlFor="calsrc-url">{editing ? 'new link (only if the old one stopped working)' : 'calendar link'}</label>
        <input id="calsrc-url" className="cal-inp" inputMode="url" autoComplete="off" spellCheck={false}
               value={url} onChange={(e) => setUrl(e.target.value)}
               placeholder={editing ? 'leave empty to keep the link' : 'https://… or webcal://…'}/>
      </div>
      {!editing && (
        <div className="hint">
          Google Calendar: on a computer, Settings → the calendar → “Secret address in iCal format”.
          Outlook: its calendar settings → Shared calendars → Publish a calendar → the ICS link.
          iPhone or iCloud: the calendar’s share button → Public Calendar → copy the link.
          The link is private: anyone with it can see the calendar. Domovoi keeps it on this
          server, never shows it again, and only reads the calendar: it can’t change anything there.
        </div>
      )}
      <div className="row2">
        <div style={{ flex: '1 1 200px' }}>
          <label htmlFor="calsrc-name">name</label>
          <input id="calsrc-name" className="cal-inp" value={name} maxLength={80}
                 placeholder="e.g. Alex’s work" onChange={(e) => setName(e.target.value)}/>
        </div>
        <div style={{ flex: '1 1 160px' }}>
          <label>whose</label>
          <CalPersonSelect people={people} value={person} onChange={setPerson}/>
        </div>
      </div>
      <div>
        <label>colour</label>
        <CalColorPicker value={color} onChange={setColor}/>
      </div>
      {err && <div className="err" style={{ color: 'var(--err-ink)', fontSize: 12 }}>{err}</div>}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <Button type="button" onClick={onCancel} disabled={busy}>cancel</Button>
        <Button variant="primary" type="submit" disabled={busy}>
          {busy ? (editing ? 'saving…' : 'reading the calendar…') : (editing ? 'save' : 'add calendar')}
        </Button>
      </div>
    </form>
  );
};

const CalendarsPanel = () => {
  const admin = useAdminSignedIn();
  const [fire, toastNode] = useToast();
  const { items: sources, loading, error, refresh } =
    useApiList('/api/calendar/sources', { eventTypes: ['calendar.events.changed'], refetchOnAuth: true });
  const { items: people } = useApiList('/api/people', { quiet: true });
  const [adding, setAdding] = React.useState(false);
  const [editing, setEditing] = React.useState(null);
  const [busyId, setBusyId] = React.useState(null);
  const del = useDeleteConfirm(async (s) => {
    try {
      await apiDelete(`/api/calendar/sources/${s.id}`);
      fire(`removed ${s.name}`);
      refresh();
    } catch (e) { reportMutationFailure(fire, 'remove', e); }
  });
  const refreshNow = async (s) => {
    setBusyId(s.id);
    try {
      const r = await apiPost(`/api/calendar/sources/${s.id}/refresh`, {});
      const src = r && r.source;
      if (r && r.ok) fire(`${s.name} is up to date`, { tone: 'ok' });
      else fire(`${s.name}: ${(src && src.last_error_words) || 'couldn’t refresh'}`, { tone: 'warn' });
      refresh();
    } catch (e) { reportMutationFailure(fire, 'refresh', e); }
    finally { setBusyId(null); }
  };

  const list = Array.isArray(sources) ? sources : [];
  const links = list.filter((s) => s.kind === 'link');
  const phones = list.filter((s) => s.kind === 'phone');
  const refused = error && (error.status === 401 || error.status === 403);
  const offline = links.some((s) => s.status === 'internet_off');

  const row = (s) => (
    <div key={s.id} className="calsrc-row">
      <span className="sw" style={{ '--cal-c': s.color }}/>
      <div style={{ minWidth: 0 }}>
        <div className="nm">{s.name}</div>
        <div className="sub mono">{calSourceLine(s)}</div>
        {(s.status === 'error' || (s.last_error === 'truncated')) && s.last_error_words && (
          <div className="err">{s.last_error_words}</div>
        )}
        {editing === s.id && (
          <CalLinkForm people={people} initial={s} fire={fire}
                       onDone={() => { setEditing(null); refresh(); }} onCancel={() => setEditing(null)}/>
        )}
      </div>
      <div className="acts">
        {s.kind === 'link' && admin && editing !== s.id && <>
          <Button icon="refresh-cw" disabled={busyId === s.id || s.status === 'internet_off'}
                  title={s.status === 'internet_off' ? 'internet access is turned off for this server' : undefined}
                  onClick={() => refreshNow(s)}>{busyId === s.id ? 'reading…' : 'refresh now'}</Button>
          <Button icon="pencil" onClick={() => setEditing(s.id)}>edit</Button>
          <Button icon="trash-2" style={{ color: 'var(--err-ink)' }}
                  onClick={() => del.request(s, {
                    title: `Remove ${s.name}?`, confirmLabel: 'Remove calendar',
                    body: <div>Its events leave this Domovoi. The calendar itself isn’t touched.</div>,
                  })}>remove</Button>
        </>}
      </div>
    </div>
  );

  return (
    <>
      <Card title="Calendars by link"
            sub="Show a Google, Outlook or iCloud calendar on the household calendar, read-only."
            action={admin && !adding ? <Button icon="plus" onClick={() => setAdding(true)}>add a calendar</Button> : null}
            padded>
        {offline && (
          <div className="cal-notice" style={{ marginBottom: 12 }}>
            <StatusDot tone="idle"/>
            <span className="why">
              Internet access is turned off for this server (Settings → Internet), so these calendars
              can’t refresh. The last copy of each stays on the calendar.
            </span>
          </div>
        )}
        {adding && (
          <CalLinkForm people={people} fire={fire}
                       onDone={() => { setAdding(false); refresh(); }} onCancel={() => setAdding(false)}/>
        )}
        {refused && !list.length ? <PairedOnlyEmpty what="the household's calendars"/>
          : loading && !list.length ? <div className="cal-notice">loading…</div>
          : links.length ? <div className="calsrc-list">{links.map(row)}</div>
          : !adding && <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>no calendars added by link</div>}
        {!admin && (
          <div style={{ fontSize: 11, color: 'var(--fg-faint)', marginTop: 12 }}>
            an admin can add, change or remove calendars
          </div>
        )}
      </Card>
      <Card title="Phones’ calendars"
            sub="Calendars people share from their phone with the Domovoi app, over your home network."
            padded>
        {phones.length
          ? <div className="calsrc-list">{phones.map(row)}</div>
          : <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>no phone is sharing its calendars</div>}
        <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 12, lineHeight: 1.45 }}>
          On an Android phone: the Domovoi app → Settings → Household → Phone calendars, then pick the
          calendars to share. They stay read-only here, update on their own, and leave this Domovoi
          when that phone stops sharing them.
        </div>
      </Card>
      {del.node}
      {toastNode}
    </>
  );
};

window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections.calendars = { Panel: CalendarsPanel };

/* A linked calendar that can't refresh says so, plainly, with the age of
 * the copy still shown. Phones' calendars never appear here (they send
 * their own, over the house's network). */
const CalSourceNotices = ({ sources }) => {
  const stuck = (Array.isArray(sources) ? sources : []).filter(
    (s) => s.kind === 'link' && (s.status === 'error' || s.status === 'internet_off'));
  if (!stuck.length) return null;
  return (
    <div className="cal-notices">
      {stuck.map((s) => (
        <div key={s.id} className="cal-notice">
          <StatusDot tone={s.status === 'internet_off' ? 'idle' : 'warn'}/>
          <span className="nm">{s.name}</span>
          <span className="why">
            {s.status === 'internet_off'
              ? "can't refresh: internet access is turned off"
              : (s.last_error_words || "can't refresh")}
            {s.last_refreshed_at ? ` · showing the copy from ${relTime(s.last_refreshed_at)}` : ' · no copy yet'}
          </span>
        </div>
      ))}
    </div>
  );
};

window.CalendarPage = CalendarPage;
// Home's "today" card formats times the way this page does (see the
// shared-scope note in satellites.jsx for why it borrows, not copies).
Object.assign(window, { fmtClock, fmtDayLabel });
