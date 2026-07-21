/* Calendar page — month / week / day views, full CRUD, event-detail rail.
 *
 * Data sources:
 *   * GET    /api/calendar/events            — list (no date filter; the
 *                                              backend caps at 500 and
 *                                              the UI re-windows client-
 *                                              side per view)
 *   * POST   /api/calendar/events            — create (always source='local')
 *   * PATCH  /api/calendar/events/{id}       — partial update
 *   * DELETE /api/calendar/events/{id}       — delete
 *   * /ws/state · `calendar.event.changed`   — refresh on remote change
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
const EventChip = ({ event, selected, onClick }) => {
  const isGoogle = event.source === 'google';
  return (
    <button
      type="button"
      className={`cal-chip ${isGoogle ? 'src-google' : 'src-local'} ${selected ? 'sel' : ''}`}
      onClick={(e) => { e.stopPropagation(); onClick(event.id); }}
      title={`${event.title} · ${fmtRange(event.starts_at, event.ends_at)}`}
    >
      <span className="dot"/>
      <span className="t mono">{fmtClock(event.starts_at)}</span>
      <span className="ti">{event.title}</span>
    </button>
  );
};

/* ---- Month grid ------------------------------------------------- */
const MonthGrid = ({ anchor, events, selectedId, onSelect, onPickDay }) => {
  const monthStart = startOfMonth(anchor);
  const gridStart  = startOfWeek(monthStart);
  const days = Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));
  const today = new Date();
  const eventsByDay = React.useMemo(() => {
    const m = {};
    for (const e of events) {
      const k = dateKey(new Date(e.starts_at));
      (m[k] = m[k] || []).push(e);
    }
    for (const k of Object.keys(m)) m[k].sort((a,b) => a.starts_at.localeCompare(b.starts_at));
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
  const isGoogle = event.source === 'google';
  return (
    <button
      type="button"
      className={`cal-tg-event ${isGoogle ? 'src-google' : 'src-local'} ${selected ? 'sel' : ''}`}
      style={{ top, height }}
      onClick={(e) => { e.stopPropagation(); onClick(event.id); }}
    >
      <div className="ti">{event.title}</div>
      <div className="ts mono">{fmtRange(event.starts_at, event.ends_at)}</div>
      {event.location && height > 60 && <div className="loc">{event.location}</div>}
    </button>
  );
};

const TimeColumn = ({ day, events, selectedId, onSelect }) => {
  const dayEvents = events.filter(e => sameDay(new Date(e.starts_at), day));
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
                  <div>{fmtClock(e.starts_at)}</div>
                  {e.ends_at && <div className="end">{fmtClock(e.ends_at)}</div>}
                </div>
                <div className="body">
                  <div className="ti">{e.title}</div>
                  <div className="meta">
                    {e.location && <span><Icon name="map-pin" size={11}/> {e.location}</span>}
                  </div>
                </div>
                <Pill tone={e.source === 'google' ? 'idle' : 'live'}>{e.source}</Pill>
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
};

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
  const isGoogle = event.source === 'google';
  return (
    <aside className="cal-detail">
      <div className="cal-detail-head">
        <div className={`src-stripe ${isGoogle ? 'src-google' : 'src-local'}`}/>
        <div style={{ flex: 1 }}>
          <div className="ttl">{event.title}</div>
          <div className="when mono">{fmtDateLong(new Date(event.starts_at)).toLowerCase()}</div>
          {event.ends_at && <div className="when mono">{fmtRange(event.starts_at, event.ends_at)}</div>}
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
          <Icon name={isGoogle ? 'cloud' : 'pencil'} size={14}/>
          <div>
            <Pill tone={isGoogle ? 'idle' : 'live'}>{event.source}</Pill>
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
      <div className="cal-detail-foot">
        <Button icon="pencil" onClick={() => onEdit(event)}>edit</Button>
        <Button icon="trash-2" onClick={() => onDelete(event)} style={{ color: 'var(--err)' }}>delete</Button>
      </div>
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
const CalendarPage = () => {
  const [view, setView]         = React.useState('month');
  const [anchor, setAnchor]     = React.useState(() => new Date());
  const [selectedId, setSelId]  = React.useState(null);
  const [modalOpen, setModalOp] = React.useState(false);
  const [draft, setDraft]       = React.useState(() => blankDraft(new Date()));
  const [fire, toastNode]       = useToast();

  const { items: events, refresh: refreshEvents } =
    useApiList('/api/calendar/events', { eventTypes: ['calendar.events.changed'] });

  const visible = React.useMemo(() => {
    if (view === 'month') {
      const ms = startOfMonth(anchor);
      const gs = startOfWeek(ms);
      const ge = addDays(gs, 42);
      return events.filter(e => { const t = new Date(e.starts_at); return t >= gs && t < ge; });
    }
    if (view === 'week') {
      const ws = startOfWeek(anchor);
      const we = addDays(ws, 7);
      return events.filter(e => { const t = new Date(e.starts_at); return t >= ws && t < we; });
    }
    return events.filter(e => sameDay(new Date(e.starts_at), anchor));
  }, [view, anchor, events]);

  const selected = events.find(e => e.id === selectedId);

  const openNew = (forDay) => {
    setDraft(blankDraft(forDay || anchor));
    setSelId(null);
    setModalOp(true);
  };
  const openEdit = (e) => { setDraft({ ...e }); setModalOp(true); };

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
      fire(`save failed: ${err.message}`);
    }
  };

  const del = async (e) => {
    try {
      await apiDelete(`/api/calendar/events/${e.id}`);
      if (selectedId === e.id) setSelId(null);
      fire(`deleted "${e.title}"`);
      refreshEvents();
    } catch (err) {
      fire(`delete failed: ${err.message}`);
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
        sub={`${events.length} events · source of truth until calendarhandler ships`}
      />

      <CalTopBar view={view} setView={setView} anchor={anchor} setAnchor={setAnchor} onNew={openNew}/>

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

window.CalendarPage = CalendarPage;
