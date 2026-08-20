/* Remote Access page — the LTL Remote plugin's dashboard page.
 *
 * Loaded by the app bootstrap per the plugin manifest ([web].scripts),
 * wrapped in an IIFE and Babel-compiled, so top-level consts here can
 * never collide with the core bundle or another plugin. The page is
 * exported ONLY through the namespaced window registry at the bottom
 * (window.DomovoiPlugins.ltl_remote.pages.RemoteAccessPage) — never a
 * bare window global.
 *
 * Data sources (all under the plugin's own API prefix):
 *   * GET  /api/plugins/ltl_remote/state        — link + pairing + quota
 *   * GET  /api/plugins/ltl_remote/devices      — approved / pending
 *   * GET  /api/plugins/ltl_remote/access-log   — metadata-only history
 *   * POST /api/plugins/ltl_remote/pairing/start · /pairing/cancel
 *   * POST /api/plugins/ltl_remote/devices/approve · /devices/revoke
 *   * POST /api/plugins/ltl_remote/token/rotate · /unlink
 *   * /ws/state · `ltl_remote.link.changed` / `ltl_remote.devices.changed`
 *
 * Core-bundle globals used (loaded before any plugin script): React,
 * Card, Button, IconButton, Icon, Pill, StatusDot, Empty, PageHeader,
 * useToast, relTime, apiGet/apiPost, useApiList, useApiObject.
 *
 * The fingerprint is the most important thing on this page. It is the
 * only defense against a substituted household key, and it only works
 * if a person actually compares it — so it is rendered large, in mono,
 * and told plainly what it is for, rather than tucked into a details
 * pane where nobody would read it.
 */

const LTL_API = '/api/plugins/ltl_remote';

/* Scoped styles.
 *
 * Plugin pages get no stylesheet of their own — the manifest ships
 * scripts, not CSS — so the handful of classes this page needs are
 * injected once, under a single `ltl-` prefix, and built entirely from
 * the core design tokens. No literal colors: light and dark themes are
 * whatever the tokens currently say they are.
 */
const LTL_STYLES = `
.ltl-page { display: flex; flex-direction: column; gap: var(--s-4, 16px); }
.ltl-grid { display: grid; gap: var(--s-4, 16px); grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.ltl-prose { color: var(--fg-muted); font-size: 0.9rem; line-height: 1.55; margin: 0 0 12px; }
.ltl-muted { color: var(--fg-subtle); }
.ltl-error { color: var(--err); }
.ltl-faint { color: var(--fg-faint); white-space: nowrap; }
.ltl-mono { font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace); }

.ltl-code {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 1.05rem; line-height: 1.6; letter-spacing: 0.02em;
  background: var(--sunken); border: 1px solid var(--border);
  border-radius: var(--r-md, 10px); padding: 14px 16px; margin-bottom: 12px;
  word-break: break-word; color: var(--fg);
}

.ltl-fingerprint {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 1.15rem; line-height: 1.7; letter-spacing: 0.04em;
  color: var(--fg); background: var(--sunken);
  border: 1px solid var(--border); border-radius: var(--r-md, 10px);
  padding: 14px 16px; margin-bottom: 12px; word-break: break-word;
}

.ltl-facts { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; margin: 0 0 14px; }
.ltl-facts dt { color: var(--fg-subtle); font-size: 0.82rem; }
.ltl-facts dd { color: var(--fg); font-size: 0.9rem; margin: 0; }

.ltl-quota { margin-bottom: 14px; }
.ltl-quota-bar { background: var(--sunken); border-radius: var(--r-full, 9999px); height: 8px; overflow: hidden; }
.ltl-quota-fill { background: var(--brand); height: 100%; border-radius: inherit; transition: width 0.3s ease; }
.ltl-quota-fill[data-over="true"] { background: var(--warn); }
.ltl-quota-text { color: var(--fg-subtle); font-size: 0.82rem; margin-top: 6px; }

.ltl-actions { display: flex; flex-wrap: wrap; gap: 8px; }

.ltl-section { margin-bottom: 18px; }
.ltl-section:last-child { margin-bottom: 0; }
.ltl-section-title { color: var(--fg-subtle); font-size: 0.78rem; letter-spacing: 0.04em; margin-bottom: 8px; }

.ltl-device {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 10px 0; border-bottom: 1px solid var(--border-soft);
}
.ltl-device:last-child { border-bottom: none; }
.ltl-device-label { color: var(--fg); font-size: 0.92rem; }
.ltl-device-meta { color: var(--fg-subtle); font-size: 0.78rem; margin-top: 2px; }
.ltl-device-actions { display: flex; gap: 6px; flex-shrink: 0; }

.ltl-log { overflow-x: auto; }
.ltl-log table { border-collapse: collapse; width: 100%; font-size: 0.84rem; }
.ltl-log th {
  text-align: left; color: var(--fg-subtle); font-weight: 500;
  font-size: 0.76rem; letter-spacing: 0.03em;
  padding: 6px 12px 6px 0; border-bottom: 1px solid var(--border-strong);
}
.ltl-log td { padding: 7px 12px 7px 0; border-bottom: 1px solid var(--border-soft); color: var(--fg); }
.ltl-log tr[data-outcome="denied"] td { color: var(--warn); }
.ltl-log tr[data-outcome="error"] td { color: var(--err); }

@media (max-width: 640px) {
  .ltl-device { flex-direction: column; align-items: flex-start; }
}
`;



const CONNECTION_TONE = {
  connected: 'ok',
  connecting: 'warn',
  disconnected: 'err',
  revoked: 'err',
  idle: 'idle',
};

const CONNECTION_LABEL = {
  connected: 'connected',
  connecting: 'connecting',
  disconnected: 'offline',
  revoked: 'revoked',
  idle: 'not started',
};

const fmtBytes = (n) => {
  if (n === null || n === undefined) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = Number(n);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
};

/* ---- Pairing ---------------------------------------------------- */
/*
 * Shown only while the server is unpaired. The code is generated on
 * this machine and only its hash goes to LTL, which is worth saying on
 * the page — it is the reason typing eight words into a website is a
 * safe thing to do.
 */
const PairingCard = ({ state, onChanged, fire }) => {
  const [busy, setBusy] = React.useState(false);

  const start = async () => {
    setBusy(true);
    try {
      await apiPost(`${LTL_API}/pairing/start`, {});
      onChanged();
    } catch (e) {
      fire(e.message || 'could not reach Lazy Thumb Labs', 'err');
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    setBusy(true);
    try {
      await apiPost(`${LTL_API}/pairing/cancel`, {});
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  if (!state.pairing_code) {
    return (
      <Card title="pair this server">
        <p className="ltl-prose">
          Remote access lets devices you approve reach this dashboard and
          the voice API from outside the house. This server dials out —
          nothing is opened on your router.
        </p>
        <p className="ltl-prose">
          Pairing produces an eight-word code. Only a hash of it is sent to
          Lazy Thumb Labs, so the code itself never leaves this machine.
        </p>
        <Button variant="primary" icon="link" onClick={start} disabled={busy}>
          {busy ? 'starting…' : 'Get a pairing code'}
        </Button>
      </Card>
    );
  }

  return (
    <Card title="pairing code">
      <p className="ltl-prose">
        Sign in at <strong>lazythumblabs.com</strong>, choose <em>Add a
        household</em>, and enter this code.
      </p>
      <div className="ltl-code">{state.pairing_code}</div>
      <p className="ltl-prose ltl-muted">
        Expires {relTime(state.pairing_expires_at)}. Pairing completes on
        its own once you enter it — you can leave this page.
      </p>
      <Button icon="x" onClick={cancel} disabled={busy}>Cancel pairing</Button>
    </Card>
  );
};

/* ---- Fingerprint ------------------------------------------------ */
/*
 * Deliberately prominent. This string is what a user compares against
 * the one their phone shows; if the two ever differ, something is
 * sitting in the middle. Burying it would make the check theatre.
 */
const FingerprintCard = ({ fingerprint }) => (
  <Card title="this server's fingerprint">
    <div className="ltl-fingerprint">{fingerprint || '—'}</div>
    <p className="ltl-prose ltl-muted">
      Every device shows this same string when it connects. If a device
      ever shows something different, stop and don't approve it — that is
      what a key substitution looks like.
    </p>
  </Card>
);

/* ---- Link status ------------------------------------------------ */
const LinkCard = ({ state, onChanged, fire }) => {
  const [busy, setBusy] = React.useState(false);
  const tone = CONNECTION_TONE[state.connection_state] || 'idle';
  const label = CONNECTION_LABEL[state.connection_state] || state.connection_state;
  const used = state.quota_used_bytes || 0;
  const limit = state.quota_limit_bytes;
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : null;

  const act = async (path, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true);
    try {
      await apiPost(`${LTL_API}/${path}`, {});
      onChanged();
    } catch (e) {
      fire(e.message || 'that did not work', 'err');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="link"
      action={<Pill tone={tone} live={state.connection_state === 'connected'}>{label}</Pill>}
    >
      <dl className="ltl-facts">
        <dt>account</dt>
        <dd>{state.account_label || state.household_id || '—'}</dd>
        <dt>plan</dt>
        <dd>{state.plan_code || '—'}</dd>
        <dt>connected</dt>
        <dd>{state.last_connected_at ? relTime(state.last_connected_at) : '—'}</dd>
      </dl>

      {limit ? (
        <div className="ltl-quota">
          <div className="ltl-quota-bar">
            <div
              className="ltl-quota-fill"
              style={{ width: `${pct}%` }}
              data-over={pct >= 90 ? 'true' : 'false'}
            />
          </div>
          <div className="ltl-quota-text">
            {fmtBytes(used)} of {fmtBytes(limit)} this period
            {state.quota_period_end ? ` · resets ${relTime(state.quota_period_end)}` : ''}
          </div>
        </div>
      ) : null}

      {state.last_error ? (
        <p className="ltl-prose ltl-error">{state.last_error}</p>
      ) : null}

      <div className="ltl-actions">
        <Button
          icon="refresh-cw"
          disabled={busy}
          onClick={() => act('token/rotate',
            'Replace this server’s relay token? The link drops and reconnects. '
            + 'Approved devices are not affected.')}
        >
          Rotate relay token
        </Button>
        <Button
          variant="danger"
          icon="unlink"
          disabled={busy}
          onClick={() => act('unlink',
            'Unlink this server from Lazy Thumb Labs? Remote access stops and every '
            + 'device has to be approved again. Nothing about this house changes on your LAN.')}
        >
          Unlink
        </Button>
      </div>
    </Card>
  );
};

/* ---- Devices ---------------------------------------------------- */
/*
 * The local trust decision lives here. A device registered through the
 * LTL website shows up as pending and can reach nothing at all until
 * someone presses Approve on this page.
 */
const DeviceRow = ({ device, onChanged, fire }) => {
  const [busy, setBusy] = React.useState(false);

  const act = async (verb) => {
    setBusy(true);
    try {
      await apiPost(`${LTL_API}/devices/${verb}`, { device_id: device.device_id });
      onChanged();
      fire(`${device.label} ${verb === 'approve' ? 'approved' : 'revoked'}`);
    } catch (e) {
      fire(e.message || 'that did not work', 'err');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ltl-device">
      <div className="ltl-device-main">
        <div className="ltl-device-label">{device.label}</div>
        <div className="ltl-device-meta">
          <span className="ltl-mono">{device.fingerprint}</span>
          {device.last_seen_at ? <span> · seen {relTime(device.last_seen_at)}</span> : null}
          {device.last_seen_country ? <span> · {device.last_seen_country}</span> : null}
        </div>
      </div>
      <div className="ltl-device-actions">
        {device.status === 'pending' ? (
          <React.Fragment>
            <Button variant="primary" disabled={busy} onClick={() => act('approve')}>
              Approve
            </Button>
            <Button disabled={busy} onClick={() => act('revoke')}>Deny</Button>
          </React.Fragment>
        ) : (
          <Button disabled={busy} onClick={() => act('revoke')}>Revoke</Button>
        )}
      </div>
    </div>
  );
};

const DevicesCard = ({ devices, loading, onChanged, fire }) => {
  const pending = devices.filter((d) => d.status === 'pending');
  const approved = devices.filter((d) => d.status === 'approved');

  return (
    <Card title="devices" action={pending.length ? <Pill tone="warn">{pending.length} waiting</Pill> : null}>
      {pending.length > 0 && (
        <div className="ltl-section">
          <div className="ltl-section-title">waiting for approval</div>
          <p className="ltl-prose ltl-muted">
            Check the fingerprint against the one shown on the device before
            approving. An approved device can do anything a device on your
            home network can do.
          </p>
          {pending.map((d) => (
            <DeviceRow key={d.device_id} device={d} onChanged={onChanged} fire={fire} />
          ))}
        </div>
      )}

      <div className="ltl-section">
        <div className="ltl-section-title">approved</div>
        {approved.length === 0 && !loading ? (
          <Empty title="No approved devices" sub="Add one from the Lazy Thumb Labs app, then approve it here." />
        ) : (
          approved.map((d) => (
            <DeviceRow key={d.device_id} device={d} onChanged={onChanged} fire={fire} />
          ))
        )}
      </div>
    </Card>
  );
};

/* ---- Access log ------------------------------------------------- */
/*
 * Metadata only, by design: method, path, status, size. No bodies and
 * no query strings are ever stored, so this answers "what did that
 * device do while I was away" without becoming a second copy of the
 * household's data.
 */
const AccessLogCard = ({ entries, loading }) => (
  <Card title="remote activity" sub="method, path and size only — no request contents are stored">
    {entries.length === 0 && !loading ? (
      <Empty title="Nothing yet" sub="Remote requests will show up here." />
    ) : (
      <div className="ltl-log">
        <table>
          <thead>
            <tr>
              <th>when</th><th>device</th><th>request</th><th>result</th><th>size</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} data-outcome={e.outcome}>
                <td className="ltl-faint">{relTime(e.at)}</td>
                <td>{e.device_id || '—'}</td>
                <td className="ltl-mono">{e.method} {e.path}</td>
                <td>{e.outcome === 'ok' ? e.status : (e.denial_code || e.outcome)}</td>
                <td className="ltl-faint">{fmtBytes(e.bytes_out)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )}
  </Card>
);

/* ---- Page ------------------------------------------------------- */
const RemoteAccessPage = () => {
  const [fire, toastNode] = useToast();

  const { data: state, loading: stateLoading, refresh: refreshState } =
    useApiObject(`${LTL_API}/state`, { eventTypes: ['ltl_remote.link.changed'] });

  const { items: devices, loading: devicesLoading, refresh: refreshDevices } =
    useApiList(`${LTL_API}/devices`, { eventTypes: ['ltl_remote.devices.changed'] });

  const { items: log, loading: logLoading } =
    useApiList(`${LTL_API}/access-log?limit=100`,
               { eventTypes: ['ltl_remote.devices.changed'] });

  const link = state || {};
  const paired = Boolean(link.household_id);

  const onChanged = () => { refreshState(); refreshDevices(); };

  return (
    <div className="ltl-page">
      <style>{LTL_STYLES}</style>
      <PageHeader
        title="Remote Access"
        sub="reach this house from outside it — outbound only, encrypted end to end"
      />

      {stateLoading ? null : (
        <div className="ltl-grid">
          {paired
            ? <LinkCard state={link} onChanged={onChanged} fire={fire} />
            : <PairingCard state={link} onChanged={onChanged} fire={fire} />}
          <FingerprintCard fingerprint={link.fingerprint} />
        </div>
      )}

      {paired && (
        <React.Fragment>
          <DevicesCard
            devices={devices}
            loading={devicesLoading}
            onChanged={onChanged}
            fire={fire}
          />
          <AccessLogCard entries={log} loading={logLoading} />
        </React.Fragment>
      )}

      {toastNode}
    </div>
  );
};

/* Namespaced window registry — the only global export. */
window.DomovoiPlugins = window.DomovoiPlugins || {};
window.DomovoiPlugins.ltl_remote = window.DomovoiPlugins.ltl_remote || {};
window.DomovoiPlugins.ltl_remote.pages = Object.assign(
  {}, window.DomovoiPlugins.ltl_remote.pages, { RemoteAccessPage },
);
