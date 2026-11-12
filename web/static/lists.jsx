/* Lists page — the household's lists (V048): to-do, shopping, any list it names.
 *
 * The same lists voice keeps ("add milk to the shopping list"), so an item
 * said to a room shows up here a moment later: every write fires
 * `lists.changed` on /ws/state and this page reads again. A tab that comes
 * back into view reads again too (a browser that lost the socket).
 *
 * Data sources (web/backend/api/lists.py):
 *   * GET    /api/lists                            — the lists, with counts
 *   * POST   /api/lists                            — new list {name}
 *   * GET    /api/lists/{id}                       — one list and its items
 *   * PATCH  /api/lists/{id}                       — rename {name}
 *   * DELETE /api/lists/{id}                       — delete (asks first)
 *   * POST   /api/lists/{id}/items                 — add {text}
 *   * PATCH  /api/lists/{id}/items/{item}          — {text} or {done}
 *   * DELETE /api/lists/{id}/items/{item}          — remove one
 *   * PUT    /api/lists/{id}/items/order           — {item_ids}
 *   * POST   /api/lists/{id}/clear-done            — remove the crossed-off ones
 *
 * Layout: the lists beside the open one on a desktop, a row of chips above
 * it on a phone. Tap the box to cross an item off; tap its words to change
 * them; drag the grip (or focus it and press the arrow keys) to move it.
 * Every top-level name starts with `lists`/`Lists` (one shared script
 * scope: domovoi/tests/test_web_script_scope.py). */

const LISTS_SELECTED_KEY = 'domovoi-lists-selected';
const listsReadSelected = () => {
  try { const v = Number(window.localStorage.getItem(LISTS_SELECTED_KEY)); return Number.isFinite(v) && v > 0 ? v : null; }
  catch { return null; }
};
const listsSaveSelected = (id) => {
  try { window.localStorage.setItem(LISTS_SELECTED_KEY, String(id)); } catch { /* a convenience only */ }
};
const listsThings = (n) => (n === 1 ? '1 thing' : `${n} things`);

/* Who added an item, when the server knows: "Alex · kitchen", "by voice · garage". */
const listsAddedBy = (it) => {
  if (it.added_via !== 'voice') return null;
  const who = it.added_by_name || 'by voice';
  return it.added_by_room ? `${who} · ${it.added_by_room}` : who;
};

/* ---- The lists: a column on a desktop, chips on a phone ---------- */
const ListsSwitcher = ({ lists, selectedId, onSelect, onNew }) => {
  const [adding, setAdding] = React.useState(false);
  const [name, setName] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const submit = async (e) => {
    if (e) e.preventDefault();
    const n = name.trim();
    if (!n || busy) return;
    setBusy(true);
    const ok = await onNew(n);
    setBusy(false);
    if (ok) { setName(''); setAdding(false); }
  };
  return (
    <nav className="lists-switcher" aria-label="lists">
      <div className="lists-switcher-rows" role="tablist">
        {lists.map((l) => (
          <button key={l.id} type="button" role="tab" aria-selected={l.id === selectedId}
                  className={`lists-switcher-row ${l.id === selectedId ? 'on' : ''}`}
                  onClick={() => onSelect(l.id)}>
            <span className="lists-switcher-name">{l.name}</span>
            {l.open_count > 0 && <span className="lists-switcher-count num-tab">{l.open_count}</span>}
          </button>
        ))}
        {!adding && (
          <button type="button" className="lists-switcher-row lists-switcher-new" onClick={() => setAdding(true)}>
            <Icon name="plus" size={14}/><span>new list</span>
          </button>
        )}
      </div>
      {adding && (
        <form className="lists-new-form" onSubmit={submit}>
          <input className="lists-inp" autoFocus value={name} maxLength={60}
                 placeholder="list name" aria-label="new list name"
                 onChange={(e) => setName(e.target.value)}
                 onKeyDown={(e) => { if (e.key === 'Escape') { setAdding(false); setName(''); } }}/>
          <Button type="submit" variant="primary" disabled={!name.trim() || busy}>make</Button>
          <Button type="button" onClick={() => { setAdding(false); setName(''); }}>cancel</Button>
        </form>
      )}
    </nav>
  );
};

/* ---- One item ---------------------------------------------------- */
const ListsItemRow = ({ item, onToggle, onEdit, onDelete, grip, rowRef, dragging }) => {
  const [editing, setEditing] = React.useState(false);
  const [text, setText] = React.useState(item.text);
  React.useEffect(() => { if (!editing) setText(item.text); }, [item.text, editing]);
  const save = () => {
    setEditing(false);
    const t = text.trim();
    if (t && t !== item.text) onEdit(item, t);
    else setText(item.text);
  };
  const by = listsAddedBy(item);
  return (
    <li ref={rowRef} className={`lists-item ${item.done ? 'done' : ''} ${dragging ? 'dragging' : ''}`}>
      {grip}
      <button type="button" className="lists-check" role="checkbox" aria-checked={item.done}
              aria-label={item.done ? `put ${item.text} back on the list` : `cross off ${item.text}`}
              onClick={() => onToggle(item)}>
        {item.done && <Icon name="check" size={14}/>}
      </button>
      <div className="lists-item-body">
        {editing ? (
          <input className="lists-inp lists-item-edit" autoFocus value={text} maxLength={200}
                 aria-label="item text"
                 onChange={(e) => setText(e.target.value)}
                 onBlur={save}
                 onKeyDown={(e) => {
                   if (e.key === 'Enter') { e.preventDefault(); save(); }
                   if (e.key === 'Escape') { setText(item.text); setEditing(false); }
                 }}/>
        ) : (
          <button type="button" className="lists-item-text" title="change the words"
                  onClick={() => setEditing(true)}>{item.text}</button>
        )}
        {by && !editing && <div className="lists-item-by">{by}</div>}
      </div>
      <IconButton name="trash-2" aria-label={`remove ${item.text}`} title="remove" onClick={() => onDelete(item)}/>
    </li>
  );
};

/* ---- The open list ------------------------------------------------ */
const ListsDetail = ({ list, items, fire, refresh, onRename, onDeleteList }) => {
  const [text, setText] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [local, setLocal] = React.useState(items);
  const [drag, setDrag] = React.useState(null);           // { id, order: [ids] }
  const [renaming, setRenaming] = React.useState(false);
  const [newName, setNewName] = React.useState(list.name);
  const [showDone, setShowDone] = React.useState(true);
  const rows = React.useRef(new Map());
  // Take the server's items whenever they change (by what they say, not by
  // reference: a re-read that returns the same items changes nothing).
  const itemsSig = items.map((i) => `${i.id}:${i.done ? 1 : 0}:${i.position}:${i.text}`).join('|');
  React.useEffect(() => { if (!drag) setLocal(items); }, [itemsSig, drag]);
  React.useEffect(() => { setNewName(list.name); setRenaming(false); }, [list.id, list.name]);

  const base = `/api/lists/${list.id}`;
  const openItems = local.filter((i) => !i.done);
  const doneItems = local.filter((i) => i.done);
  const shownOpen = drag ? drag.order.map((id) => openItems.find((i) => i.id === id)).filter(Boolean) : openItems;

  const add = async (e) => {
    if (e) e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    try {
      const r = await apiPost(`${base}/items`, { text: t });
      setText('');
      if (r && r.outcome === 'already') fire(`"${r.item.text}" is already on the list`);
      else if (r && r.outcome === 'reopened') fire(`"${r.item.text}" is back on the list`);
      refresh();
    } catch (err) {
      reportMutationFailure(fire, 'add', err, { kept: true });
    } finally {
      setBusy(false);
    }
  };
  const toggle = async (it) => {
    setLocal((cur) => cur.map((x) => (x.id === it.id ? { ...x, done: !it.done } : x)));
    try { await apiPatch(`${base}/items/${it.id}`, { done: !it.done }); }
    catch (err) { reportMutationFailure(fire, 'save', err); }
    refresh();
  };
  const edit = async (it, t) => {
    setLocal((cur) => cur.map((x) => (x.id === it.id ? { ...x, text: t } : x)));
    try { await apiPatch(`${base}/items/${it.id}`, { text: t }); }
    catch (err) { reportMutationFailure(fire, 'save', err); }
    refresh();
  };
  const remove = async (it) => {
    setLocal((cur) => cur.filter((x) => x.id !== it.id));
    try { await apiDelete(`${base}/items/${it.id}`); }
    catch (err) { reportMutationFailure(fire, 'delete', err); }
    refresh();
  };
  const clearDone = async () => {
    try {
      const r = await apiPost(`${base}/clear-done`, {});
      fire(`removed ${listsThings((r && r.removed) || 0)}`);
    } catch (err) { reportMutationFailure(fire, 'clear', err); }
    refresh();
  };
  const saveOrder = async (order) => {
    const ids = openItems.map((i) => i.id);
    if (order.join(',') === ids.join(',')) return;
    setLocal((cur) => [...order.map((id) => cur.find((x) => x.id === id)).filter(Boolean), ...cur.filter((x) => x.done)]);
    try { await apiPut(`${base}/items/order`, { item_ids: order }); }
    catch (err) { reportMutationFailure(fire, 'move', err); }
    refresh();
  };

  // Drag by the grip: pointer events, so a mouse and a finger both work.
  const startDrag = (e, id) => {
    if (e.button != null && e.button !== 0) return;
    e.preventDefault();
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* older browsers */ }
    setDrag({ id, order: openItems.map((i) => i.id) });
  };
  const moveDrag = (e) => {
    if (!drag) return;
    const y = e.clientY;
    const others = drag.order.filter((id) => id !== drag.id);
    let at = others.length;
    for (let k = 0; k < others.length; k += 1) {
      const el = rows.current.get(others[k]);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      if (y < r.top + r.height / 2) { at = k; break; }
    }
    const order = [...others.slice(0, at), drag.id, ...others.slice(at)];
    if (order.join(',') !== drag.order.join(',')) setDrag({ ...drag, order });
  };
  const endDrag = () => {
    if (!drag) return;
    const order = drag.order;
    setDrag(null);
    saveOrder(order);
  };
  const keyMove = (e, id) => {
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
    e.preventDefault();
    const ids = openItems.map((i) => i.id);
    const k = ids.indexOf(id);
    const to = e.key === 'ArrowUp' ? k - 1 : k + 1;
    if (k < 0 || to < 0 || to >= ids.length) return;
    ids.splice(k, 1);
    ids.splice(to, 0, id);
    saveOrder(ids);
  };
  const gripFor = (it) => (
    <button type="button" className="lists-grip" aria-label={`move ${it.text}: arrow keys`}
            title="drag to move" onPointerDown={(e) => startDrag(e, it.id)}
            onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}
            onKeyDown={(e) => keyMove(e, it.id)}>
      <Icon name="grip-vertical" size={14}/>
    </button>
  );
  const rename = async (e) => {
    if (e) e.preventDefault();
    const n = newName.trim();
    if (!n || n === list.name) { setRenaming(false); return; }
    if (await onRename(list, n)) setRenaming(false);
  };

  return (
    <section className="card lists-card">
      <div className="card-head lists-head">
        {renaming ? (
          <form className="lists-rename" onSubmit={rename}>
            <input className="lists-inp" autoFocus value={newName} maxLength={60} aria-label="list name"
                   onChange={(e) => setNewName(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Escape') { setRenaming(false); setNewName(list.name); } }}/>
            <Button type="submit" variant="primary">save</Button>
            <Button type="button" onClick={() => { setRenaming(false); setNewName(list.name); }}>cancel</Button>
          </form>
        ) : (
          <>
            <div>
              <div className="t">{list.name}</div>
              <div className="s">{listsThings(openItems.length)} to go{doneItems.length ? ` · ${doneItems.length} crossed off` : ''}</div>
            </div>
            <div className="lists-head-actions">
              <IconButton name="pencil" aria-label="rename list" title="rename" onClick={() => setRenaming(true)}/>
              <IconButton name="trash-2" aria-label="delete list" title="delete list" onClick={() => onDeleteList(list, local.length)}/>
            </div>
          </>
        )}
      </div>
      <form className="lists-add" onSubmit={add}>
        <input className="lists-inp" value={text} maxLength={200} placeholder={`add to ${list.name.toLowerCase()}`}
               aria-label={`add to ${list.name}`} onChange={(e) => setText(e.target.value)}/>
        <Button type="submit" variant="primary" icon="plus" disabled={!text.trim() || busy}>add</Button>
      </form>
      {local.length === 0 ? (
        <Empty title="nothing on this list" sub={`type above, or say "add milk to the ${list.name.toLowerCase()} list"`}/>
      ) : (
        <>
          <ul className="lists-items">
            {shownOpen.map((it) => (
              <ListsItemRow key={it.id} item={it} onToggle={toggle} onEdit={edit} onDelete={remove}
                            grip={gripFor(it)} dragging={drag && drag.id === it.id}
                            rowRef={(el) => { if (el) rows.current.set(it.id, el); else rows.current.delete(it.id); }}/>
            ))}
          </ul>
          {openItems.length === 0 && <div className="lists-all-done">everything here is crossed off</div>}
          {doneItems.length > 0 && (
            <div className="lists-done">
              <div className="lists-done-head">
                <button type="button" className="lists-done-toggle" aria-expanded={showDone}
                        onClick={() => setShowDone(!showDone)}>
                  <Icon name={showDone ? 'chevron-down' : 'chevron-right'} size={14}/>
                  crossed off · {doneItems.length}
                </button>
                <Button icon="eraser" onClick={clearDone}>clear crossed off</Button>
              </div>
              {showDone && (
                <ul className="lists-items">
                  {doneItems.map((it) => (
                    <ListsItemRow key={it.id} item={it} onToggle={toggle} onEdit={edit} onDelete={remove}
                                  grip={<span className="lists-grip-space" aria-hidden/>}/>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
};

/* ---- Page -------------------------------------------------------- */
const ListsPage = () => {
  const [fire, toastNode] = useToast();
  const { items: lists, loading, error, refresh: refreshLists } =
    useApiList('/api/lists', { eventTypes: ['lists.changed'] });
  const [selectedId, setSelectedId] = React.useState(listsReadSelected);
  const selected = lists.find((l) => l.id === selectedId) || lists[0] || null;
  const detailPath = selected ? `/api/lists/${selected.id}` : null;
  const { data: detail, refresh: refreshDetail } =
    useApiObject(detailPath, { eventTypes: ['lists.changed'] });
  const refreshAll = React.useCallback(() => { refreshLists(); refreshDetail(); }, [refreshLists, refreshDetail]);

  // A tab that comes back into view reads again: the socket may have
  // dropped while it was hidden.
  React.useEffect(() => {
    if (typeof document === 'undefined' || !document.addEventListener) return undefined;
    const onShow = () => { if (document.visibilityState === 'visible') refreshAll(); };
    document.addEventListener('visibilitychange', onShow);
    return () => document.removeEventListener('visibilitychange', onShow);
  }, [refreshAll]);

  const select = (id) => { setSelectedId(id); listsSaveSelected(id); };

  const makeList = async (name) => {
    try {
      const made = await apiPost('/api/lists', { name });
      if (made && made.id) select(made.id);
      refreshLists();
      return true;
    } catch (err) {
      reportMutationFailure(fire, 'make list', err, { kept: true });
      return false;
    }
  };
  const renameList = async (list, name) => {
    try {
      await apiPatch(`/api/lists/${list.id}`, { name });
      refreshAll();
      return true;
    } catch (err) {
      reportMutationFailure(fire, 'rename', err, { kept: true });
      return false;
    }
  };
  const del = useDeleteConfirm(async (list) => {
    try {
      await apiDelete(`/api/lists/${list.id}`);
      fire(`deleted ${list.name}`);
      if (selectedId === list.id) setSelectedId(null);
      refreshLists();
    } catch (err) {
      reportMutationFailure(fire, 'delete', err);
    }
  });
  const askDelete = (list, count) => del.request(list, {
    title: `Delete ${list.name}?`,
    confirmLabel: 'Delete list',
    body: <div>{count ? `The list and the ${listsThings(count)} on it go for good.` : 'The list goes for good.'} This can’t be undone.</div>,
  });

  const refused = error && (error.status === 401 || error.status === 403);
  const total = lists.reduce((n, l) => n + l.open_count, 0);
  let body;
  if (refused && lists.length === 0) body = <PairedOnlyEmpty what="the household's lists"/>;
  else if (loading && lists.length === 0) body = <div className="lists-quiet">loading lists…</div>;
  else if (!selected) {
    body = <Empty title="no lists yet" sub='make one here, or say "make a list called camping"'/>;
  } else if (!detail || !detail.list || detail.list.id !== selected.id) {
    body = <div className="lists-quiet">loading {selected.name.toLowerCase()}…</div>;
  } else {
    body = (
      <ListsDetail list={detail.list} items={detail.items || []} fire={fire} refresh={refreshAll}
                   onRename={renameList} onDeleteList={askDelete}/>
    );
  }

  return (
    <div className="page lists-page">
      <PageHeader title="Lists"
                  sub={lists.length ? `${lists.length} lists · ${listsThings(total)} to go` : 'to-do, shopping and your own'}/>
      <div className="lists-layout">
        <ListsSwitcher lists={lists} selectedId={selected ? selected.id : null} onSelect={select} onNew={makeList}/>
        <div className="lists-main">{body}</div>
      </div>
      {del.node}
      {toastNode}
    </div>
  );
};

window.ListsPage = ListsPage;
