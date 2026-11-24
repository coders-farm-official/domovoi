/* Friend Houses → "Apps with this friend" (design FRIEND-HOUSES-PLUGINS §9, §6.11).
 *
 * Live: wired to the plugin mail service's routes, all on the security
 * tier at both hops (admin only):
 *
 *   GET  /api/friend-houses/friends/{id}/apps            the toggle, each app, this friend's notices
 *   PUT  /api/friend-houses/friends/{id}/apps            {on, apps}: turn on (with the apps ticked) or off
 *   PUT  /api/friend-houses/friends/{id}/apps/{slug}     {allowed}: allow (or confirm) or revoke one app
 *   POST /api/friend-houses/friends/{id}/apps/{slug}/resume   end a pause
 *   GET  /api/friend-houses/apps/notices                 every notice: paused, re-confirm, an app
 *                                                         failing with several friends, Docker
 *
 * The pieces are drawn only where an admin is: a friend's row in the
 * Friend Houses page's admin zone (FhFriendApps), the notices card at the
 * top of that zone (FhAppsNoticesCard), and Home's admin rows
 * (fhAppsHomeRows, home.jsx). Nothing here is spoken.
 *
 * No misleading controls: "Turn on" is drawn only when an installed app
 * can use it; an app the friend's house doesn't have says so; a pause
 * says why, with Resume.
 *
 * SECURITY: a friend's name, an app's name and every notice render as
 * plain text through React's escaping, as in friend_houses.jsx.
 *
 * Globals this file adds (one shared scope): FH_APPS_*, fhApps*, FhApps*
 * and FhFriendApps. No object rest (`{ a, ...rest }`): see printers.jsx.
 */

const FH_APPS_API = '/api/friend-houses';
const FH_APPS_TITLE = 'Apps with this friend';
const FH_APPS_CONFIRM_TEXT = 'Their house will see which of these apps this house has, and each app you allow reads what that friend sends it.';
const FH_APPS_OFF_TEXT = 'Turning it off stops these apps exchanging anything with them and deletes the mail waiting either way.';
const fhAppsMuted = { fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 };
const fhAppsRow = { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' };

const fhAppsFor = (secs) => {
  const min = Math.max(1, Math.round((Number(secs) || 0) / 60));
  if (min < 60) return `${min} ${min === 1 ? 'minute' : 'minutes'}`;
  const h = Math.round(min / 60);
  return `${h} ${h === 1 ? 'hour' : 'hours'}`;
};
const fhAppsName = (a) => String((a && (a.name || a.app || a.slug)) || 'This app');
const fhAppsErr = (e) => String((e && (e.detail || e.message)) || 'it didn’t work');

/* One app in a friend's list: allowed or not, and whether their house has it. */
const FhAppsRow = ({ app, busy, onAllow }) => (
  <div className="fh-apps-row" style={{ ...fhAppsRow, padding: '6px 0' }}>
    <label style={{ ...fhAppsRow, gap: 8, flex: '1 1 200px' }}>
      <input type="checkbox" checked={!!app.allowed} disabled={busy}
             aria-label={`allow ${fhAppsName(app)}`}
             onChange={(e) => onAllow(app.slug, e.target.checked)}/>
      <span style={{ fontSize: 13 }}>{fhAppsName(app)}</span>
    </label>
    {app.both_have_it
      ? <Pill tone="ok">both houses have it</Pill>
      : <span style={fhAppsMuted}>{`their house hasn’t said it has ${fhAppsName(app)}`}</span>}
    {app.needs_confirm && <Pill tone="warn">waiting for you to allow it</Pill>}
    {app.paused && <Pill tone="warn">{`paused ${fhAppsFor(app.paused_for_sec)}`}</Pill>}
  </div>
);

/* The confirm before turning it on: the permission holders, all ticked. */
const FhAppsConfirm = ({ friendName, apps, busy, onConfirm, onCancel }) => {
  const [picked, setPicked] = React.useState(() => (apps || []).map((a) => a.slug));
  const flip = (slug, on) => setPicked((was) => (on ? was.concat([slug]) : was.filter((s) => s !== slug)));
  return (
    <div className="fh-apps-confirm" style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '8px 0' }}>
      <strong style={{ fontSize: 13 }}>{`Turn on apps with ${String(friendName || 'this friend')}?`}</strong>
      <div style={fhAppsMuted}>{FH_APPS_CONFIRM_TEXT}</div>
      {(apps || []).map((a) => (
        <label key={a.slug} style={{ ...fhAppsRow, gap: 8 }}>
          <input type="checkbox" checked={picked.indexOf(a.slug) >= 0} disabled={busy}
                 aria-label={`allow ${fhAppsName(a)}`}
                 onChange={(e) => flip(a.slug, e.target.checked)}/>
          <span style={{ fontSize: 13 }}>{fhAppsName(a)}</span>
        </label>
      ))}
      <div style={fhAppsRow}>
        <Button variant="primary" disabled={busy || !picked.length} onClick={() => onConfirm(picked)}>Turn on</Button>
        <Button variant="ghost" disabled={busy} onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
};

/* A plugin installed (or upgraded) with the permission reaches nobody until
 * an admin allows it: "Games can use Apps with friends. Allow it with: …" */
const FhAppsReconfirm = ({ app, friends, busy, onAllow, onLater }) => {
  const [picked, setPicked] = React.useState(() => (friends || []).map((f) => f.id));
  const flip = (id, on) => setPicked((was) => (on ? was.concat([id]) : was.filter((x) => x !== id)));
  return (
    <div className="fh-apps-reconfirm" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <strong style={{ fontSize: 13 }}>{`${fhAppsName(app)} can use Apps with friends. Allow it with:`}</strong>
      {(friends || []).map((f) => (
        <label key={f.id} style={{ ...fhAppsRow, gap: 8 }}>
          <input type="checkbox" checked={picked.indexOf(f.id) >= 0} disabled={busy}
                 aria-label={`allow with ${String(f.name || '')}`}
                 onChange={(e) => flip(f.id, e.target.checked)}/>
          <span style={{ fontSize: 13 }}>{String(f.name || '')}</span>
        </label>
      ))}
      <div style={fhAppsRow}>
        <Button variant="primary" disabled={busy || !picked.length} onClick={() => onAllow(picked)}>Allow</Button>
        <Button variant="ghost" disabled={busy} onClick={onLater}>Not now</Button>
      </div>
    </div>
  );
};

/* The breaker's notices (§6.7): the server's own words for each pause
 * (the four causes), Resume now or turn it off; "may need an update" for
 * an app paused with several friends. Never spoken. */
const FhAppsPaused = ({ paused, many, busy, onResume, onTurnOff }) => {
  const list = paused || [];
  const plugins = many || [];
  if (!list.length && !plugins.length) return null;
  return (
    <div className="fh-apps-paused" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {plugins.map((n) => (
        <div key={`many-${n.slug}`} style={{ ...fhAppsMuted, color: 'var(--warn-ink)' }}>{String(n.text || '')}</div>
      ))}
      {list.map((p) => (
        <div key={`${p.friend_id}-${p.slug}`} style={fhAppsRow}>
          <Icon name="alert-triangle" size={13}/>
          <span style={{ fontSize: 13, flex: '1 1 240px' }}>{String(p.text || '')}</span>
          <Button disabled={busy} onClick={() => onResume(p.friend_id, p.slug)}>Resume now</Button>
          <Button variant="ghost" disabled={busy} onClick={() => onTurnOff(p.friend_id)}>
            {`Turn off apps with ${String(p.friend || 'them')}`}
          </Button>
        </div>
      ))}
    </div>
  );
};

/* A friend's "Apps with this friend" block: the toggle, its confirm, the
 * apps list. Presentational: state and actions come from the caller. */
const FhAppsSection = ({ friend, data, busy, confirming, onAsk, onConfirm, onCancel, onTurnOff, onAllow }) => {
  const d = data || {};
  const apps = d.apps || [];
  const name = String((friend && friend.name) || 'this friend');
  return (
    <div className="fh-apps" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={fhAppsRow}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{FH_APPS_TITLE}</span>
        {d.apps_on
          ? <Button disabled={busy} onClick={onTurnOff}>Turn off</Button>
          : (!confirming && apps.length > 0 && <Button disabled={busy} onClick={onAsk}>Turn on</Button>)}
      </div>
      {!d.apps_on && !apps.length && <div style={fhAppsMuted}>no installed app can be used with friends</div>}
      {!d.apps_on && confirming && (
        <FhAppsConfirm friendName={name} apps={apps} busy={busy} onConfirm={onConfirm} onCancel={onCancel}/>
      )}
      {d.apps_on && (
        <>
          <div style={fhAppsMuted}>{FH_APPS_OFF_TEXT}</div>
          {apps.map((a) => <FhAppsRow key={a.slug} app={a} busy={busy} onAllow={onAllow}/>)}
        </>
      )}
    </div>
  );
};

/* The actions, shared by a friend's block and the notices card. */
const fhAppsActions = (fire, done, setBusy) => {
  const act = async (fn, ok) => {
    setBusy(true);
    try {
      await fn();
      if (ok) fire(ok, { tone: 'ok' });
      done();
    } catch (e) {
      fire(fhAppsErr(e), { tone: 'warn' });
    } finally { setBusy(false); }
  };
  const friendPath = (id) => `${FH_APPS_API}/friends/${encodeURIComponent(id)}/apps`;
  return {
    turnOn: (id, picked) => act(() => apiPut(friendPath(id), { on: true, apps: picked }), 'Apps with this friend are on'),
    turnOff: (id) => act(() => apiPut(friendPath(id), { on: false }), 'Apps with this friend are off'),
    allow: (id, slug, on) => act(() => apiPut(`${friendPath(id)}/${encodeURIComponent(slug)}`, { allowed: !!on }),
                                 on ? 'Allowed' : 'Turned off for this friend'),
    resume: (id, slug) => act(() => apiPost(`${friendPath(id)}/${encodeURIComponent(slug)}/resume`, {}), 'Resumed'),
  };
};

/* A friend's block, live (friend_houses.jsx draws it in each friend's row,
 * in the admin zone). */
const FhFriendApps = ({ friend, fire, onChanged }) => {
  const id = friend && friend.id;
  const view = useApiObject(id ? `${FH_APPS_API}/friends/${encodeURIComponent(id)}/apps` : null,
                            { quiet: true, refetchOnAuth: true });
  const [confirming, setConfirming] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const say = fire || (() => {});
  const done = () => { setConfirming(false); view.refresh(); if (onChanged) onChanged(); };
  const a = fhAppsActions(say, done, setBusy);
  const d = view.data;
  if (!d) return null;
  const notices = d.notices || [];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <FhAppsSection friend={friend} data={d} busy={busy} confirming={confirming}
                     onAsk={() => setConfirming(true)} onCancel={() => setConfirming(false)}
                     onConfirm={(picked) => a.turnOn(id, picked)}
                     onTurnOff={() => a.turnOff(id)}
                     onAllow={(slug, on) => a.allow(id, slug, on)}/>
      <FhAppsPaused paused={notices.filter((n) => n.kind === 'paused')} busy={busy}
                    onResume={(fid, slug) => a.resume(fid, slug)} onTurnOff={(fid) => a.turnOff(fid)}/>
    </div>
  );
};

/* The re-confirm prompts, one per app: every friend it waits for, ticked. */
const fhAppsReconfirms = (notices) => {
  const by = {};
  (notices || []).filter((n) => n.kind === 'confirm').forEach((n) => {
    const e = by[n.slug] || (by[n.slug] = { app: { slug: n.slug, name: n.app }, friends: [] });
    e.friends.push({ id: n.friend_id, name: n.friend });
  });
  return Object.keys(by).sort().map((k) => by[k]);
};

/* Every notice, at the top of the Friend Houses page's admin zone. Draws
 * nothing when there is nothing to say. */
const FhAppsNoticesCard = ({ fire, onChanged }) => {
  const notes = useApiObject(`${FH_APPS_API}/apps/notices`, { quiet: true, refetchOnAuth: true });
  const [busy, setBusy] = React.useState(false);
  const [later, setLater] = React.useState([]);
  const say = fire || (() => {});
  const done = () => { notes.refresh(); if (onChanged) onChanged(); };
  const a = fhAppsActions(say, done, setBusy);
  const list = (notes.data && notes.data.notices) || [];
  const paused = list.filter((n) => n.kind === 'paused');
  const many = list.filter((n) => n.kind === 'plugin');
  const docker = list.filter((n) => n.kind === 'docker');
  const asks = fhAppsReconfirms(list).filter((r) => later.indexOf(r.app.slug) < 0);
  if (!paused.length && !many.length && !docker.length && !asks.length) return null;
  const allowAll = async (slug, ids) => {
    setBusy(true);
    try {
      for (const id of ids) {
        await apiPut(`${FH_APPS_API}/friends/${encodeURIComponent(id)}/apps/${encodeURIComponent(slug)}`, { allowed: true });
      }
      say('Allowed', { tone: 'ok' });
      done();
    } catch (e) { say(fhAppsErr(e), { tone: 'warn' }); } finally { setBusy(false); }
  };
  return (
    <Card title="Apps with friends" sub="paused mail and apps waiting for you">
      <div className="fh-apps-notices" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {docker.map((n, i) => (
          <div key={`docker-${i}`} style={fhAppsRow}>
            <Icon name="alert-triangle" size={13}/>
            <span style={{ fontSize: 13 }}>{String(n.text || '')}</span>
          </div>
        ))}
        <FhAppsPaused paused={paused} many={many} busy={busy}
                      onResume={(fid, slug) => a.resume(fid, slug)} onTurnOff={(fid) => a.turnOff(fid)}/>
        {asks.map((r) => (
          <FhAppsReconfirm key={r.app.slug} app={r.app} friends={r.friends} busy={busy}
                           onAllow={(ids) => allowAll(r.app.slug, ids)}
                           onLater={() => setLater((was) => was.concat([r.app.slug]))}/>
        ))}
      </div>
    </Card>
  );
};

/* Home's rows for an admin (home.jsx): one per notice, linking to the
 * Friend Houses page. A household member never asks for them. */
const fhAppsHomeRows = (data) => {
  const list = (data && data.notices) || [];
  const asks = fhAppsReconfirms(list);
  const rows = list.filter((n) => n.kind !== 'confirm').map((n, i) => ({
    key: `fh-apps-${n.kind}-${n.friend_id || ''}-${n.slug || i}`, tone: 'warn', scope: 'admin',
    text: String(n.text || ''), href: '#settings/friend-houses',
  }));
  return rows.concat(asks.map((r) => ({
    key: `fh-apps-confirm-${r.app.slug}`, tone: 'brand', scope: 'admin',
    text: `${fhAppsName(r.app)} can use Apps with friends: allow it with ${r.friends.length} ${r.friends.length === 1 ? 'friend' : 'friends'}?`,
    href: '#settings/friend-houses',
  })));
};
