/* /doc.html: one document in the Documents editor, full screen.
 *
 * The phone app opens this page in its own in-app view to edit a document
 * (android: ui/screens/documents/WebDocEditor.kt), and a browser can open
 * it directly: /doc.html?path=<path inside Documents>.
 *
 * Credentials: inside the app, the household token comes from the app
 * itself (window.DomovoiApp.deviceToken(), a bridge the app offers only to
 * its own server's pages), so it is never put in an address. A browser
 * uses what it already holds, or a `device_token` in the address, which is
 * checked and then removed from the address bar (as display.html does).
 *
 * The app's bridge, when present (all optional):
 *   DomovoiApp.deviceToken() → the household token
 *   DomovoiApp.close()       → leave the editor (after it has saved)
 *   DomovoiApp.saveFile(name, mime, base64) / DomovoiApp.print(html, title)
 *                              → Download/share and PDF (doc_editor.jsx)
 * and the app calls window.DomovoiDocPage.back() for its Back button.
 */
(() => {
  const params = new URLSearchParams(window.location.search);
  const path = (params.get('path') || '').trim();
  const app = window.DomovoiApp || null;

  const tokenAccepted = async (candidate) => {
    const headers = {};
    headers[(typeof Auth !== 'undefined' && Auth.DEVICE_TOKEN_HEADER) || 'X-Device-Token'] = candidate;
    try {
      const r = await fetch(`${API_BASE}/api/documents?kind=doc`, { credentials: 'omit', cache: 'no-store', headers });
      return r.ok;
    } catch (e) { return false; }
  };
  const adopt = (candidate, trusted) => {
    if (!candidate || typeof Auth === 'undefined' || !Auth.pair) return Promise.resolve();
    let stored = null;
    try { stored = Auth.deviceToken ? Auth.deviceToken() : null; } catch (e) { stored = null; }
    if (stored === candidate) return Promise.resolve();
    if (!stored || trusted) { try { Auth.pair(candidate); } catch (e) { /* keep what is stored */ } return Promise.resolve(); }
    return tokenAccepted(candidate).then((ok) => { if (ok) { try { Auth.pair(candidate); } catch (e) { /* keep */ } } });
  };

  let ready = Promise.resolve();
  if (app && typeof app.deviceToken === 'function') {
    let t = null;
    try { t = app.deviceToken(); } catch (e) { t = null; }
    ready = adopt(t, true);
  } else if (params.has('device_token')) {
    ready = adopt((params.get('device_token') || '').trim(), false);
    params.delete('device_token');
    try {
      const rest = params.toString();
      window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`);
    } catch (e) { /* the address keeps it; harmless */ }
  }

  const leave = () => {
    if (app && typeof app.close === 'function') { app.close(); return; }
    if (window.history.length > 1) window.history.back();
    else window.location.href = '/#files';
  };

  const DocPage = () => {
    const [fire, toastNode] = useToast();
    const [go, setGo] = React.useState(false);
    const closeRef = React.useRef(null);
    React.useEffect(() => { ready.then(() => setGo(true)); }, []);
    React.useEffect(() => {
      window.DomovoiDocPage = { back: () => (closeRef.current ? closeRef.current() : leave()) };
      return () => { delete window.DomovoiDocPage; };
    }, []);
    if (!path) {
      return <div style={{ padding: 24, fontSize: 14 }}>No document was named. Open one from Files.</div>;
    }
    if (!go) return <div style={{ padding: 24, fontSize: 13, color: 'var(--fg-faint)' }}>Opening…</div>;
    return (
      <React.Fragment>
        <window.DocEditor rel_path={path} onClose={leave} fire={fire} embedded closeRef={closeRef}/>
        {toastNode}
      </React.Fragment>
    );
  };

  document.title = path ? path.split('/').pop().replace(/\.(html?|md|markdown|docx)$/i, '') : 'Document';
  ReactDOM.createRoot(document.getElementById('root')).render(<DocPage/>);
})();
