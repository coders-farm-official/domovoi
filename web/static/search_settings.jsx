/* Settings → Chat & AI → Search (assistant plan part 5, W1).
 *
 * Where Domovoi looks things up, and the household's own say in it:
 *
 *   - "Always allow internet" (LOOKUP_ALWAYS_ALLOW, off by default): off,
 *     every chat shows what it would send and where, and asks first;
 *   - the sources in the order they are read (GET /api/chat/lookup-sources:
 *     this server's own first, then Wikipedia and Wikidata, then the open
 *     web), which an admin can reorder (LOOKUP_SOURCE_ORDER). Reordering
 *     never changes which ones ask before going online;
 *   - "Use your own search": this server's search helper, your own SearXNG
 *     address, or Brave Search with your own key (write-only, like the
 *     AcoustID key: never shown again once saved), each with a Test button
 *     (POST /api/config/search-test). A key goes only to its service.
 *
 * Writes are an admin's (PATCH /api/config/editable); everyone else reads
 * the state. Under internet "never" only the house's own sources answer.
 */

const SEARCH_OWN_CHOICES = [
  { value: '', label: 'This server’s search helper',
    sub: 'SearXNG on your Domovoi server asks public search engines for you.' },
  { value: 'searxng', label: 'Your own SearXNG',
    sub: 'A SearXNG you run, with its JSON output on (search.formats: json).' },
  { value: 'brave', label: 'Brave Search, with your key',
    sub: 'Brave’s Search API. You pay Brave after its monthly free credit.' },
];

const SEARCH_CLASS_WORDS = {
  local: 'on this server, never asks',
  api: 'public API, asks first',
  web: 'open web, asks first',
};

/* Brave's terms as they apply here (checked 2026-10-21 on brave.com/search/api
 * and its API terms of use). Plain words, no promises. */
const SEARCH_BRAVE_TERMS = [
  'Brave charges for each search from prepaid credit, after a free amount each month; it asks for a card.',
  'Brave sells AI answers separately (its “Answers” plan) and points chatbots to its LLM Context service. Domovoi uses the plain web search: check Brave’s terms cover how you use it.',
  'Its terms don’t let you share your key or keep a store of results. Domovoi keeps neither: the key stays on this server and goes only to Brave.',
];

const SearchSettingsPanel = () => {
  const admin = useAdminSignedIn();
  const [fire, toastNode] = useToast();
  const { data: srcInfo, refresh: refreshSources } = useApiObject('/api/chat/lookup-sources', { quiet: true });
  const { data: cfg, refresh: refreshCfg } = useApiObject(admin ? '/api/config/editable' : null,
                                                          { quiet: true, refetchOnAuth: true });
  const fields = {};
  ((cfg && cfg.fields) || []).forEach((f) => { fields[f.name] = f; });
  const internetOff = srcInfo && srcInfo.internet === 'never';
  const [saving, setSaving] = React.useState(false);
  const [own, setOwn] = React.useState(null);        // the choice being edited
  const [url, setUrl] = React.useState('');
  const [key, setKey] = React.useState('');
  const [test, setTest] = React.useState(null);
  const [testing, setTesting] = React.useState(false);

  const ownSaved = fields.lookup_own_search ? (fields.lookup_own_search.value || '') : (srcInfo && srcInfo.own_search) || '';
  const choice = own == null ? ownSaved : own;
  React.useEffect(() => {
    if (fields.lookup_own_searxng_url && typeof fields.lookup_own_searxng_url.value === 'string') {
      setUrl(fields.lookup_own_searxng_url.value);
    }
  }, [cfg]);
  React.useEffect(() => { setTest(null); }, [choice, url, key]);

  const save = async (changes, said) => {
    setSaving(true);
    try {
      const res = await apiPatch('/api/config/editable', { changes });
      const rejected = res && res.rejected && Object.keys(res.rejected).length ? res.rejected : null;
      if (rejected) fire(`not saved: ${Object.values(rejected)[0]}`);
      else if (said) fire(said);
      refreshCfg(); refreshSources();
      return !rejected;
    } catch (e) {
      fire(mutationErrorText(e, 'Save') || 'could not save');
      return false;
    } finally {
      setSaving(false);
    }
  };

  const alwaysOn = srcInfo ? !!srcInfo.always_allow : false;
  const sources = (srcInfo && srcInfo.sources) || [];
  const move = (i, by) => {
    const order = sources.map((s) => s.slug);
    const j = i + by;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    save({ lookup_source_order: order.join(', ') }, 'search order saved');
  };
  const resetOrder = () => save({ lookup_source_order: '' }, 'back to the usual order');

  const saveOwn = async () => {
    const changes = { lookup_own_search: choice };
    if (choice === 'searxng') changes.lookup_own_searxng_url = url.trim();
    if (choice === 'brave' && key.trim()) changes.lookup_brave_key = key.trim();
    if (choice === 'brave' && !key.trim() && !(fields.lookup_brave_key && fields.lookup_brave_key.is_set)) {
      fire('paste your Brave Search key first');
      return;
    }
    if (await save(changes, 'your search is saved')) { setOwn(null); setKey(''); }
  };
  const clearKey = () => save({ lookup_brave_key: '' }, 'the Brave key is gone');

  const runTest = async () => {
    if (testing) return;
    setTesting(true); setTest(null);
    try {
      const body = choice === 'brave' ? { kind: 'brave', ...(key.trim() ? { key: key.trim() } : {}) }
        : { kind: 'searxng', ...(url.trim() ? { url: url.trim() } : {}) };
      const r = await apiPost('/api/config/search-test', body);
      setTest({ tone: r && r.result === 'works' ? 'ok' : 'err', text: (r && r.detail) || 'no answer' });
    } catch (e) {
      setTest({ tone: 'err', text: isInternetOffError(e) ? INTERNET_OFF_MESSAGE : (mutationErrorText(e, 'Test', { kept: false }) || 'the test failed') });
    } finally {
      setTesting(false);
    }
  };
  const toneColor = { ok: 'var(--ok-ink, var(--ok))', err: 'var(--err-ink, var(--err))' };
  const keySet = !!(fields.lookup_brave_key && fields.lookup_brave_key.is_set);

  return (
    <div className="search-settings" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {internetOff && (
        <div className="chat-note info" role="note">
          This Domovoi stays off the internet (<a href="#settings/internet">Settings → Internet</a>): lookups
          read only its manual and your offline library, and nothing on this page goes online.
        </div>
      )}
      {!admin && (
        <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
          An admin can change these.{' '}
          <button type="button" className="home-link"
                  onClick={() => { try { Auth.requestLogin(); } catch { /* no auth module */ } }}>sign in</button>
        </div>
      )}

      <Card title="Asking first">
        <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 13, lineHeight: 1.5 }}>
          <input type="checkbox" checked={alwaysOn} disabled={!admin || saving || internetOff}
                 onChange={(e) => save({ lookup_always_allow: e.target.checked },
                   e.target.checked ? 'chats look things up without asking' : 'chats ask first again')}/>
          <span>
            <strong>Always allow internet</strong>
            <span style={{ display: 'block', fontSize: 12, color: 'var(--fg-muted)' }}>
              Off: before a chat looks something up online it shows the exact words and where they go, and
              asks: allow once, allow in this chat, or not now. On: chats look things up without asking.
              Domovoi’s manual and your offline library never need asking.
            </span>
          </span>
        </label>
      </Card>

      <Card title="Where Domovoi looks" sub="Read in this order, the first that answers first.">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {sources.length === 0 && <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>couldn’t read the sources</div>}
          {sources.map((s, i) => (
            <div key={s.slug} className="search-source-row"
                 style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
              <span className="mono" style={{ width: 18, color: 'var(--fg-muted)' }}>{i + 1}</span>
              <Icon name={s.lookup_class === 'local' ? 'house' : 'globe'} size={14}/>
              <span style={{ flex: 1, minWidth: 0 }}>
                {s.name}
                <span style={{ display: 'block', fontSize: 11, color: 'var(--fg-muted)' }}>
                  {SEARCH_CLASS_WORDS[s.lookup_class] || s.lookup_class}
                </span>
              </span>
              {admin && (
                <React.Fragment>
                  <IconButton name="arrow-up" title={`read ${s.name} sooner`} aria-label={`read ${s.name} sooner`}
                              onClick={() => move(i, -1)}/>
                  <IconButton name="arrow-down" title={`read ${s.name} later`} aria-label={`read ${s.name} later`}
                              onClick={() => move(i, 1)}/>
                </React.Fragment>
              )}
            </div>
          ))}
          {admin && srcInfo && (srcInfo.order || []).length > 0 && (
            <div><Button onClick={resetOrder} disabled={saving}>back to the usual order</Button></div>
          )}
        </div>
      </Card>

      <Card title="Use your own search" sub="Where open-web lookups go. Wikipedia is still read first.">
        <div role="radiogroup" aria-label="use your own search" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {SEARCH_OWN_CHOICES.map((c) => (
            <label key={c.value || 'helper'} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 13 }}>
              <input type="radio" name="search-own" checked={choice === c.value} disabled={!admin || internetOff}
                     onChange={() => setOwn(c.value)}/>
              <span>{c.label}
                <span style={{ display: 'block', fontSize: 12, color: 'var(--fg-muted)' }}>{c.sub}</span>
              </span>
            </label>
          ))}
        </div>
        {admin && choice === 'searxng' && (
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ fontSize: 12, color: 'var(--fg-muted)' }}>Your SearXNG address
              <input type="url" className="cal-inp" value={url} placeholder="http://192.168.1.20:8888"
                     onChange={(e) => setUrl(e.target.value)} style={{ display: 'block', width: '100%' }}/>
            </label>
          </div>
        )}
        {admin && choice === 'brave' && (
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
              Brave Search key {keySet ? '(one is set: paste a new one to replace it)' : ''}
              <input type="password" autoComplete="off" className="cal-inp" value={key}
                     placeholder={keySet ? '•••• set' : 'paste the key'}
                     onChange={(e) => setKey(e.target.value)} style={{ display: 'block', width: '100%' }}/>
            </label>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
              {SEARCH_BRAVE_TERMS.map((t) => <li key={t}>{t}</li>)}
            </ul>
            <div style={{ fontSize: 12 }}>
              <a href="https://brave.com/search/api/" target="_blank" rel="noopener noreferrer">Brave Search API and its terms</a>
              {keySet && <> · <button type="button" className="home-link" onClick={clearKey}>remove the saved key</button></>}
            </div>
          </div>
        )}
        {admin && (
          <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 12 }}>
            <Button variant="primary" onClick={saveOwn} disabled={saving || internetOff || choice === ownSaved && !key.trim()
              && !(choice === 'searxng' && fields.lookup_own_searxng_url && url !== (fields.lookup_own_searxng_url.value || ''))}>
              save
            </Button>
            {choice && !internetOff && (
              <Button onClick={runTest} disabled={testing || (choice === 'brave' && !key.trim() && !keySet)
                || (choice === 'searxng' && !url.trim())}>{testing ? 'testing…' : 'test'}</Button>
            )}
            {test && <span className="search-test-result" style={{ color: toneColor[test.tone] }}>{test.text}</span>}
          </div>
        )}
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
          If your own search fails, the answer says so and comes from Wikipedia instead. Nothing you set here is
          sent to Coders Farm.
        </div>
      </Card>
      {toastNode}
    </div>
  );
};

// Settings shows Chat & AI → Search once this is registered (settings.jsx
// settingsExtraSections).
window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections.search = { Panel: SearchSettingsPanel };
