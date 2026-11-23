/* Friend Houses → "Apps with this friend" (design FRIEND-HOUSES-PLUGINS §9, §6.11).
 *
 * HIDDEN. These are the dashboard pieces for apps with friends, built
 * before the plugin mail service (build slice 4) that they control. Until
 * slice 4 wires them to its routes they render only TEST DATA, so they
 * stay out of every page the household can open (no misleading controls):
 *
 *   fhAppsShown() is true only while window.DOMOVOI_FH_APPS_PREVIEW === true.
 *   Nothing in the dashboard sets it (index.html, data.js and the server
 *   never do); the component tests set it in their sandbox, and a developer
 *   may set it in a browser's console to look at the pieces. While
 *   it is off nothing below is drawn, no route is called and no nav entry,
 *   settings section or Home card exists for it.
 *
 * Even with the flag on, the pieces call nothing: every action goes to a
 * callback the caller passes, and the preview wrapper (FhFriendApps) keeps
 * its own copy of FH_APPS_FIXTURE in local state.
 *
 * Slice 4 replaces the fixture with GET /api/friend-houses/friends/{id}/apps
 * and the actions with PUT .../apps, PUT .../apps/{slug} and
 * POST .../apps/{slug}/resume (security tier at both hops), then removes
 * the flag.
 *
 * SECURITY: a friend's name and every string about their house render as
 * plain text through React's escaping, as in friend_houses.jsx.
 *
 * Globals this file adds (one shared scope): fhAppsShown, FH_APPS_*,
 * FhApps* and FhFriendApps. No object rest (`{ a, ...rest }`): see
 * printers.jsx.
 */

const fhAppsShown = () => typeof window !== 'undefined' && !!window && window.DOMOVOI_FH_APPS_PREVIEW === true;

const FH_APPS_TITLE = 'Apps with this friend';
const FH_APPS_CONFIRM_TEXT = 'Their house will see which of these apps this house has, and each app you allow reads what that friend sends it.';
const FH_APPS_OFF_TEXT = 'Turning it off stops these apps exchanging anything with them and deletes the mail waiting either way.';
const fhAppsMuted = { fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 };
const fhAppsRow = { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' };

/* Test data in the shape slice 4's GET .../apps will answer. */
const FH_APPS_FIXTURE = {
  apps_on: true,
  apps: [
    { slug: 'games', name: 'Games', allowed: true, theirs: true, paused_until: null },
    { slug: 'garden', name: 'Garden', allowed: true, theirs: false, paused_until: null },
    { slug: 'ranch', name: 'Ranch', allowed: false, theirs: true, paused_until: null, reconfirm: true },
  ],
  paused: [],
};

const fhAppsPauseLeft = (iso) => {
  const t = Date.parse(iso || '');
  if (!Number.isFinite(t)) return '';
  const min = Math.max(1, Math.round((t - Date.now()) / 60000));
  if (min < 60) return `${min} ${min === 1 ? 'minute' : 'minutes'}`;
  const h = Math.round(min / 60);
  return `${h} ${h === 1 ? 'hour' : 'hours'}`;
};

/* One app in a friend's list: allowed or not, and whether their house has it. */
const FhAppsRow = ({ app, busy, onAllow }) => (
  <div className="fh-apps-row" style={{ ...fhAppsRow, padding: '6px 0' }}>
    <label style={{ ...fhAppsRow, gap: 8, flex: '1 1 200px' }}>
      <input type="checkbox" checked={!!app.allowed} disabled={busy}
             aria-label={`allow ${String(app.name || app.slug)}`}
             onChange={(e) => onAllow(app.slug, e.target.checked)}/>
      <span style={{ fontSize: 13 }}>{String(app.name || app.slug)}</span>
    </label>
    {app.theirs
      ? <Pill tone="ok">both houses have it</Pill>
      : <span style={fhAppsMuted}>{`needs the ${String(app.name || app.slug)} plugin`}</span>}
    {app.paused_until && <Pill tone="warn">{`paused ${fhAppsPauseLeft(app.paused_until)}`}</Pill>}
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
                 aria-label={`allow ${String(a.name || a.slug)}`}
                 onChange={(e) => flip(a.slug, e.target.checked)}/>
          <span style={{ fontSize: 13 }}>{String(a.name || a.slug)}</span>
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
  const name = String((app && (app.name || app.slug)) || 'This app');
  return (
    <div className="fh-apps-reconfirm" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <strong style={{ fontSize: 13 }}>{`${name} can use Apps with friends. Allow it with:`}</strong>
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

/* The breaker's notice (§6.7): never spoken; Resume now, or turn it off. */
const fhAppsPausedText = (p) => {
  const left = fhAppsPauseLeft(p.paused_until);
  return `Mail from ${String(p.friend || 'a friend house')} to ${String(p.app || 'an app')} is paused`
    + (left ? ` for ${left}` : '') + ': it kept failing. Other friends aren’t affected.';
};
const fhAppsManyText = (app, n) => `${String(app || 'This app')} has failed on mail from ${n} friends; it may need an update.`;

const FhAppsPaused = ({ paused, busy, onResume, onTurnOff }) => {
  const list = paused || [];
  if (!list.length) return null;
  const perApp = {};
  list.forEach((p) => { perApp[p.app] = (perApp[p.app] || 0) + 1; });
  return (
    <div className="fh-apps-paused" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {Object.keys(perApp).filter((a) => perApp[a] >= 2).map((a) => (
        <div key={`many-${a}`} style={{ ...fhAppsMuted, color: 'var(--warn-ink)' }}>{fhAppsManyText(a, perApp[a])}</div>
      ))}
      {list.map((p) => (
        <div key={`${p.friend_id}-${p.slug}`} style={fhAppsRow}>
          <Icon name="alert-triangle" size={13}/>
          <span style={{ fontSize: 13, flex: '1 1 240px' }}>{fhAppsPausedText(p)}</span>
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
          : (!confirming && <Button disabled={busy || !apps.length} onClick={onAsk}>Turn on</Button>)}
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

/* The preview wrapper friend_houses.jsx draws (flag on only), over
 * FH_APPS_FIXTURE in local state. */
const FhFriendApps = ({ friend }) => {
  const [data, setData] = React.useState(() => JSON.parse(JSON.stringify(FH_APPS_FIXTURE)));
  const [confirming, setConfirming] = React.useState(false);
  if (!fhAppsShown()) return null;
  const allow = (slug, on) => setData((d) => ({ ...d, apps: d.apps.map((a) => (a.slug === slug ? { ...a, allowed: on } : a)) }));
  return (
    <FhAppsSection friend={friend} data={data} busy={false} confirming={confirming}
                   onAsk={() => setConfirming(true)} onCancel={() => setConfirming(false)}
                   onConfirm={(picked) => { setConfirming(false); setData((d) => ({ ...d, apps_on: true, apps: d.apps.map((a) => ({ ...a, allowed: picked.indexOf(a.slug) >= 0 })) })); }}
                   onTurnOff={() => setData((d) => ({ ...d, apps_on: false }))}
                   onAllow={allow}/>
  );
};
