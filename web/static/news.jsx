/* News page — per-person topics of interest, their feeds, saved items,
 * and the current briefing.
 *
 * Organized BY PERSON (answers "whose topics are whose"):
 *   * left rail  — people picker (roster from /api/people).
 *   * main       — the selected person's briefing (top) + topic manager
 *                  (category chips + free-form, each expandable to its feeds
 *                  with origin + a validity dot + add/remove) + their saved
 *                  feed (news_items, newest first, favorited pinned).
 *
 * Data sources:
 *   * GET  /api/people                                   — roster.
 *   * GET  /api/news/categories                          — category chip set.
 *   * GET  /api/news/people/{id}/topics                  — their topics.
 *   * GET  /api/news/topics/{tid}/feeds                  — a topic's feeds.
 *   * GET  /api/news/people/{id}/items                   — their saved feed.
 *   * GET  /api/news/people/{id}/briefing                — current digest.
 *   * POST/DELETE topics, feeds; POST item favorite; POST /api/news/poll.
 *   * /ws/state · `news.changed`                         — push refresh.
 */

const validityTone = (feed) => (feed.valid ? 'live' : 'idle');

/* ---- A single topic row, expandable to its feeds ------------- */
const TopicRow = ({ topic, onRemove, fire }) => {
  const [open, setOpen] = React.useState(false);
  const [feeds, setFeeds] = React.useState(null);
  const [newUrl, setNewUrl] = React.useState('');
  const [busy, setBusy] = React.useState(false);

  const loadFeeds = React.useCallback(async () => {
    try {
      setFeeds(await apiGet(`/api/news/topics/${topic.id}/feeds`));
    } catch (e) { console.warn(e); setFeeds([]); }
  }, [topic.id]);

  React.useEffect(() => { if (open && feeds === null) loadFeeds(); }, [open, feeds, loadFeeds]);

  const addFeed = async () => {
    const url = newUrl.trim();
    if (!url) return;
    setBusy(true);
    try {
      await apiPost(`/api/news/topics/${topic.id}/feeds`, { url });
      setNewUrl('');
      await loadFeeds();
      fire('feed added');
    } catch (e) { fire('couldn’t add that feed'); } finally { setBusy(false); }
  };

  const removeFeed = async (fid) => {
    try {
      await apiDelete(`/api/news/topics/${topic.id}/feeds/${fid}`);
      await loadFeeds();
    } catch (e) { fire('couldn’t remove feed'); }
  };

  const revalidate = async (fid) => {
    try { await apiPost(`/api/news/feeds/${fid}/validate`, {}); await loadFeeds(); }
    catch (e) { fire('couldn’t validate'); }
  };

  return (
    <div style={{ border: '1px solid var(--border-soft)', borderRadius: 'var(--r-sm)', marginBottom: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px' }}>
        <button onClick={() => setOpen(o => !o)}
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--fg-muted)' }}>
          <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14}/>
        </button>
        <Pill tone={topic.kind === 'category' ? 'brand' : 'idle'}>{topic.kind}</Pill>
        <span style={{ fontSize: 13, fontWeight: 500, flex: 1, minWidth: 0 }}>{topic.topic}</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
          {topic.feed_count} feed{topic.feed_count === 1 ? '' : 's'}
        </span>
        <IconButton name="x" title="remove topic" onClick={() => onRemove(topic)}/>
      </div>
      {open && (
        <div style={{ padding: '4px 10px 10px 30px', borderTop: '1px solid var(--border-soft)' }}>
          {feeds === null && <div style={{ fontSize: 12, color: 'var(--fg-muted)', padding: '6px 0' }}>loading feeds…</div>}
          {feeds !== null && feeds.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--fg-muted)', padding: '6px 0' }}>
              No feeds yet {topic.kind === 'freeform' ? '— discovery found none; add one below.' : ''}
            </div>
          )}
          {(feeds || []).map(f => (
            <div key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0' }}>
              <StatusDot tone={validityTone(f)}/>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 12, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {f.title || f.source || f.url}
                </div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--fg-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {f.discovered_via}{f.scope ? ` · ${f.scope}` : ''} · {f.url}
                </div>
              </div>
              <IconButton name="refresh-cw" title="re-check validity" onClick={() => revalidate(f.id)}/>
              <IconButton name="x" title="remove feed" onClick={() => removeFeed(f.id)}/>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
            <input value={newUrl} onChange={e => setNewUrl(e.target.value)}
                   placeholder="add RSS feed URL…"
                   onKeyDown={e => { if (e.key === 'Enter') addFeed(); }}
                   style={{ font: 'inherit', fontSize: 12, flex: 1, height: 30, padding: '0 8px',
                            borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                            background: 'var(--card)', color: 'var(--fg)' }}/>
            <Button icon="plus" onClick={addFeed} disabled={busy}>add</Button>
          </div>
        </div>
      )}
    </div>
  );
};

/* ---- Topic manager (category chips + free-form add) ---------- */
const TopicManager = ({ personId, topics, categories, onChanged, fire }) => {
  const [freeform, setFreeform] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const activeCats = new Set(topics.filter(t => t.kind === 'category').map(t => t.topic));

  const toggleCategory = async (cat) => {
    const existing = topics.find(t => t.kind === 'category' && t.topic === cat);
    try {
      if (existing) {
        await apiDelete(`/api/news/topics/${existing.id}`);
      } else {
        await apiPost(`/api/news/people/${personId}/topics`, { kind: 'category', topic: cat });
      }
      onChanged();
    } catch (e) { fire('couldn’t update category'); }
  };

  const addFreeform = async () => {
    const topic = freeform.trim();
    if (!topic) return;
    setBusy(true);
    try {
      await apiPost(`/api/news/people/${personId}/topics`, { kind: 'freeform', topic });
      setFreeform('');
      fire('topic added — discovering feeds…');
      onChanged();
    } catch (e) { fire('couldn’t add topic'); } finally { setBusy(false); }
  };

  const removeTopic = async (t) => {
    try { await apiDelete(`/api/news/topics/${t.id}`); onChanged(); }
    catch (e) { fire('couldn’t remove topic'); }
  };

  return (
    <Card title="Topics of interest" sub="Categories give broad coverage; free-form covers the niche.">
      <div style={{ padding: '12px 14px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 14 }}>
          {categories.map(cat => {
            const on = activeCats.has(cat);
            return (
              <button key={cat} onClick={() => toggleCategory(cat)}
                style={{ font: 'inherit', fontSize: 12, cursor: 'pointer', padding: '5px 11px',
                         borderRadius: 999, border: `1px solid ${on ? 'transparent' : 'var(--border)'}`,
                         background: on ? 'var(--brand)' : 'var(--card)',
                         color: on ? 'white' : 'var(--fg-muted)', textTransform: 'capitalize' }}>
                {cat}
              </button>
            );
          })}
        </div>

        <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
          <input value={freeform} onChange={e => setFreeform(e.target.value)}
                 placeholder="add a free-form topic (e.g. Formula 1, Lansing schools)…"
                 onKeyDown={e => { if (e.key === 'Enter') addFreeform(); }}
                 style={{ font: 'inherit', fontSize: 13, flex: 1, height: 34, padding: '0 10px',
                          borderRadius: 'var(--r-sm)', border: '1px solid var(--border)',
                          background: 'var(--card)', color: 'var(--fg)' }}/>
          <Button variant="primary" icon="plus" onClick={addFreeform} disabled={busy}>add topic</Button>
        </div>

        {topics.length === 0
          ? <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>No topics yet. Pick a category above or add a free-form topic.</div>
          : topics.map(t => <TopicRow key={t.id} topic={t} onRemove={removeTopic} fire={fire}/>)}
      </div>
    </Card>
  );
};

/* ---- Saved feed (news_items) --------------------------------- */
const SavedFeed = ({ personId, items, onChanged, fire }) => {
  const toggleFav = async (it) => {
    try {
      await apiPost(`/api/news/items/${it.id}/favorite`, { favorited: !it.favorited });
      onChanged();
    } catch (e) { fire('couldn’t update favorite'); }
  };
  return (
    <Card title="Saved feed" sub="Newest first. Favorited stories are pinned and never auto-deleted.">
      <div style={{ padding: items.length ? '4px 0' : 0 }}>
        {items.length === 0
          ? <div style={{ padding: 16 }}><Empty title="No stories yet" sub="Add topics, then poll now or wait for the morning fetch." glyph="sleeping"/></div>
          : items.map(it => (
            <div key={it.id} style={{ display: 'flex', gap: 10, padding: '10px 14px', borderBottom: '1px solid var(--border-soft)' }}>
              <button onClick={() => toggleFav(it)} title={it.favorited ? 'unfavorite' : 'favorite'}
                      style={{ background: 'transparent', border: 'none', cursor: 'pointer',
                               color: it.favorited ? 'var(--brand)' : 'var(--fg-faint)' }}>
                <Icon name="star" size={16}/>
              </button>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>
                  {it.url ? <a href={it.url} target="_blank" rel="noreferrer" style={{ color: 'var(--fg)', textDecoration: 'none' }}>{it.title || '(untitled)'}</a>
                          : (it.title || '(untitled)')}
                </div>
                {it.summary && <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 2, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{it.summary.replace(/<[^>]+>/g, '')}</div>}
                <div className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)', marginTop: 4 }}>
                  {it.topic ? `${it.topic} · ` : ''}{it.source || '—'} · {relTime(it.published_at || it.fetched_at)}
                  {it.read_at ? ' · read' : ' · unread'}
                </div>
              </div>
            </div>
          ))}
      </div>
    </Card>
  );
};

/* ---- Per-person News detail --------------------------------- */
const PersonNews = ({ person, categories, fire }) => {
  const pid = person.id;
  const topics = useApiList(`/api/news/people/${pid}/topics`, { eventTypes: ['news.changed'] });
  const items = useApiList(`/api/news/people/${pid}/items`, { eventTypes: ['news.changed'] });
  const briefing = useApiObject(`/api/news/people/${pid}/briefing`, { eventTypes: ['news.changed'] });
  const [polling, setPolling] = React.useState(false);

  const pollNow = async () => {
    setPolling(true);
    try {
      const r = await apiPost(`/api/news/poll?person_id=${pid}`, {});
      fire(`polled — ${r.house + r.topics} new item${(r.house + r.topics) === 1 ? '' : 's'}`);
      topics.refresh(); items.refresh(); briefing.refresh();
    } catch (e) { fire('poll failed (offline?)'); } finally { setPolling(false); }
  };

  const b = briefing.data;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <PageHeader
        title={`${person.name}’s news`}
        sub="Topics of interest, discovered feeds, and their saved stories."
        actions={<Button icon="refresh-cw" onClick={pollNow} disabled={polling}>{polling ? 'polling…' : 'poll now'}</Button>}
      />

      <Card title="Current briefing" sub={b && b.generated_at ? `generated ${relTime(b.generated_at)}` : 'the latest spoken digest'}>
        <div style={{ padding: '12px 14px', fontSize: 13, lineHeight: 1.6, color: b && b.briefing ? 'var(--fg)' : 'var(--fg-muted)' }}>
          {b && b.briefing ? b.briefing : 'No briefing yet. Turn on auto-fetch (or poll now) to generate one.'}
        </div>
      </Card>

      <TopicManager personId={pid} topics={topics.items} categories={categories}
                    onChanged={() => { topics.refresh(); items.refresh(); }} fire={fire}/>

      <SavedFeed personId={pid} items={items.items} onChanged={items.refresh} fire={fire}/>
    </div>
  );
};

/* ---- Page shell --------------------------------------------- */
const NewsPage = () => {
  const [fire, toast] = useToast();
  const people = useApiList('/api/people', { eventTypes: ['people.last_seen.changed'] });
  const categories = useApiList('/api/news/categories');
  const [selected, setSelected] = React.useState(null);

  // Default-select the most-recently-seen person once the roster loads.
  React.useEffect(() => {
    if (selected == null && people.items.length) setSelected(people.items[0].id);
  }, [people.items, selected]);

  const selectedPerson = people.items.find(p => p.id === selected) || null;

  return (
    <div className="page">
      {toast}
      <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 16, alignItems: 'start' }}>
        {/* People picker */}
        <Card title="People" sub="whose news?">
          <div style={{ padding: '6px 0' }}>
            {people.items.length === 0 && (
              <div style={{ padding: 14, fontSize: 12, color: 'var(--fg-muted)' }}>
                No people enrolled yet. Enroll a speaker on the People page first.
              </div>
            )}
            {people.items.map(p => (
              <div key={p.id} onClick={() => setSelected(p.id)}
                   style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', cursor: 'pointer',
                            background: p.id === selected ? 'var(--sunken)' : 'transparent' }}>
                <Avatar name={p.name} size="sm"/>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: p.id === selected ? 600 : 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</div>
                  <div className="mono" style={{ fontSize: 10, color: 'var(--fg-muted)' }}>heard {relTime(p.last_seen_at)}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>

        {/* Detail */}
        <div>
          {selectedPerson
            ? <PersonNews person={selectedPerson} categories={categories.items} fire={fire}/>
            : <Empty title="Pick a person" sub="Select someone on the left to manage their news." glyph="sleeping"/>}
        </div>
      </div>
    </div>
  );
};

window.NewsPage = NewsPage;
