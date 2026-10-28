/* /doc.html: one document in the Documents editor, full screen.
 *
 * The phone app opens this page in its own in-app view to edit a document
 * (android: ui/screens/documents/WebEditorHost.kt), and a browser can open
 * it directly: /doc.html?path=<path inside Documents>.
 *
 * Credentials: inside the app, the household token comes from the app
 * itself (window.DomovoiAppBridge.deviceToken(), handed over once, after
 * the app has checked this server's identity) and is held in memory only,
 * never stored and never in an address. A browser uses what it already
 * holds; a `device_token` in the address is used for this page only, and
 * only when the browser holds no token of its own, then removed from the
 * address bar.
 *
 * The app's bridge, when present (all optional):
 *   deviceToken() → the household token (once), darkTheme() → the app's theme
 *   close()       → leave the editor (after it has saved)
 *   saveFile(name, mime, base64) / print(html, title)
 *                 → Download/share and PDF (doc_editor.jsx)
 * and the app calls window.DomovoiDocPage.back() for its Back button.
 */
(() => {
  const params = new URLSearchParams(window.location.search);
  const path = (params.get('path') || '').trim();
  // The app's bridge (WebEditorHost.kt). DomovoiApp is its first name, kept
  // for builds that still use it.
  const app = window.DomovoiAppBridge || window.DomovoiApp || null;
  window.DomovoiDocHost = app;

  // Inside the app: the token the app hands over (once: the bridge gives it
  // a single time) is kept in this page's memory only, never in storage, so
  // nothing outlives the editor and a page served later from the same
  // address by another machine finds nothing.
  if (app && typeof app.deviceToken === 'function') {
    let t = null;
    try { t = app.deviceToken(); } catch (e) { t = null; }
    if (t && typeof Auth !== 'undefined' && Auth.holdDeviceTokenInMemory) Auth.holdDeviceTokenInMemory(t);
    try {
      if (typeof app.darkTheme === 'function') {
        document.documentElement.setAttribute('data-theme', app.darkTheme() ? 'dark' : 'light');
      }
    } catch (e) { /* the page's own theme */ }
  } else if (params.has('device_token')) {
    // A link with a token in it: used for this page only, and only when
    // this browser holds none of its own. It never replaces a stored token.
    const t = (params.get('device_token') || '').trim();
    let stored = null;
    try { stored = Auth.storedDeviceToken ? Auth.storedDeviceToken() : null; } catch (e) { stored = null; }
    if (t && !stored && typeof Auth !== 'undefined' && Auth.holdDeviceTokenInMemory) Auth.holdDeviceTokenInMemory(t);
    params.delete('device_token');
    try {
      const rest = params.toString();
      window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`);
    } catch (e) { /* the address keeps it; harmless */ }
  }
  const ready = Promise.resolve();

  const leave = () => {
    if (app && typeof app.close === 'function') { app.close(); return; }
    // (the token was in memory only; nothing to clear)
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
