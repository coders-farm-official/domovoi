/* Settings → System → Chat from away (1.5.0).
 *
 * Household members' phones can chat with this house's own assistant when
 * they're away from home, through a private Tor connection of the house's
 * own (no company server). A phone sets it up on the home network, from
 * the app; here an admin sees which phones have it, removes one, and turns
 * it on or off for the whole house (on by default). It needs the internet
 * setting to allow it: under "never" it is off, and the page says so.
 *
 * Data:
 *   GET    /api/away/phones                  · { on, house, phones: [{ device_id, name, platform, enrolled_at, current }] }
 *   PUT    /api/away/setting                 · { on }            (admin)
 *   DELETE /api/away/phones/{device_id}      ·                   (admin)
 */

const AWAY_HOUSE_WORDS = {
  on: 'on',
  away_off: 'off for the house',
  internet_off: 'off: the internet setting is “never”',
};

const AwayChatPanel = () => {
  const admin = useAdminSignedIn();
  const [fire, toastNode] = useToast();
  const { data, refresh } = useApiObject('/api/away/phones', { quiet: true, refetchOnAuth: true });
  const [busy, setBusy] = React.useState(false);

  const setOn = async (on) => {
    setBusy(true);
    try {
      await apiPut('/api/away/setting', { on });
      fire(on ? 'chat from away: on' : 'chat from away: off', { tone: 'ok' });
      refresh();
    } catch (e) {
      fire(`not saved: ${(e && e.message) || 'the server said no'}`, { tone: 'warn' });
    } finally { setBusy(false); }
  };

  const remove = async (p) => {
    if (!await askConfirm(`Remove chat from away for ${p.name}? It can set it up again at home.`, { confirmLabel: 'Remove' })) return;
    setBusy(true);
    try {
      await apiDelete(`/api/away/phones/${encodeURIComponent(p.device_id)}`);
      fire(`removed: ${p.name}`, { tone: 'ok' });
      refresh();
    } catch (e) {
      fire(`not removed: ${(e && e.message) || 'the server said no'}`, { tone: 'warn' });
    } finally { setBusy(false); }
  };

  if (!admin) {
    return (
      <Card title="Chat from away" sub="Phones chatting with this house when they're away from home.">
        <div style={{ padding: '12px 16px', fontSize: 13, color: 'var(--fg-muted)' }}>
          An admin can see and change this.
        </div>
      </Card>
    );
  }
  if (!data) return <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>loading…</div>;
  const phones = data.phones || [];
  return (
    <div className="away-chat" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {toastNode}
      <Card title="Chat from away"
            sub="Household members' phones can chat with Domovoi when they're away from home, over a private connection of this house's own. No company server is involved.">
        <div data-settings-mark="away-chat" style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }}>
            <input type="checkbox" role="switch" checked={!!data.on} aria-checked={!!data.on}
                   disabled={busy} aria-label="chat from away for this house"
                   onChange={(e) => setOn(!!e.target.checked)}/>
            <span>Chat from away</span>
            <Pill tone={data.house === 'on' ? 'ok' : 'idle'}>{AWAY_HOUSE_WORDS[data.house] || data.house}</Pill>
          </label>
          {data.house === 'internet_off' && (
            <div className="chat-note info" role="note">
              The internet setting (Settings → Privacy → Internet) is “never”, so phones can’t reach the house from away.
            </div>
          )}
          <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            A phone sets it up from the Domovoi app, on the home network. Turning it off here stops every phone at its next connection; their setup is kept for when it’s back on.
          </div>
        </div>
      </Card>
      <Card title="Phones" sub="The phones that set up chat from away.">
        {phones.length === 0 ? (
          <div style={{ padding: '12px 16px', fontSize: 13, color: 'var(--fg-muted)' }}>No phones yet.</div>
        ) : (
          <div role="list">
            {phones.map((p) => (
              <div role="listitem" key={p.device_id}
                   style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
                            borderTop: '1px solid var(--border)' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</div>
                  <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                    {p.platform || 'phone'} · set up {new Date(p.enrolled_at).toLocaleDateString()}
                    {!p.current && ' · not active: this device was registered again or is a shared screen'}
                  </div>
                </div>
                <Button variant="secondary" disabled={busy} onClick={() => remove(p)}>Remove</Button>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
};

window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections['away-chat'] = { Panel: AwayChatPanel };
