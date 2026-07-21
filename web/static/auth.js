/* Admin auth wiring (design §7.2/§7.3, v1 lightweight model).
 *
 * The bearer token lives ONLY in JS memory — mutations send it as
 * `Authorization: Bearer` explicitly (data.js attaches it), while the
 * HttpOnly SameSite=Strict cookie the login endpoint sets lets plain
 * GET page loads render authenticated state after a reload.
 *
 * `Auth` is a tiny global store: components.jsx's <AuthModalHost/>
 * subscribes and shows the login/setup modal whenever an API call
 * comes back 401/403 (data.js calls Auth.requestLogin()).
 *
 * Loaded before data.js in index.html.
 */

const Auth = (() => {
  let token = null;              // in-memory bearer (never persisted)
  let modalOpen = false;
  // null = not yet probed; {setup_complete, authenticated} afterwards.
  let status = null;
  const listeners = new Set();
  const notify = () => listeners.forEach((fn) => { try { fn(); } catch {} });

  const base = () => {
    try { return localStorage.getItem('domovoi-server') || ''; } catch { return ''; }
  };

  const post = async (path, body) => {
    const r = await fetch(`${base()}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include', // receive/carry the GET-state cookie
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try { data = await r.json(); } catch {}
    if (!r.ok) {
      const detail = (data && data.detail) || `${r.status} ${r.statusText}`;
      const err = new Error(detail);
      err.status = r.status;
      throw err;
    }
    return data;
  };

  return {
    get token() { return token; },
    isLoggedIn: () => !!token,
    get modalOpen() { return modalOpen; },
    get status() { return status; },

    headers() {
      return token ? { Authorization: `Bearer ${token}` } : {};
    },

    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },

    // Called by data.js on a 401/403 — pops the login modal once.
    requestLogin() {
      if (modalOpen) return;
      modalOpen = true;
      notify();
    },

    // Resolve true once a live Bearer exists (popping the login modal
    // if needed), false if the user closes the modal without signing
    // in. Lets a flow that hit a 401/403 pause, authenticate, and
    // RESUME — instead of dying after the login (the failed action was
    // previously just lost).
    ensureLoggedIn() {
      if (token) return Promise.resolve(true);
      this.requestLogin();
      return new Promise((resolve) => {
        const un = this.subscribe(() => {
          if (token) { un(); resolve(true); }
          else if (!modalOpen) { un(); resolve(false); }
        });
      });
    },
    openModal() { modalOpen = true; notify(); },
    closeModal() { modalOpen = false; notify(); },

    async refreshStatus() {
      try {
        const r = await fetch(`${base()}/api/auth/status`, { credentials: 'include' });
        if (r.ok) status = await r.json();
      } catch { /* server unreachable — leave stale */ }
      notify();
      return status;
    },

    async setup(setupCode, password) {
      const data = await post('/api/auth/setup', {
        setup_code: setupCode, password,
      });
      token = data.token || null;
      status = { setup_complete: true, authenticated: !!token };
      notify();
      return data;
    },

    async login(password, label) {
      const data = await post('/api/auth/login', {
        password, label: label || 'dashboard',
      });
      token = data.token || null;
      status = { setup_complete: true, authenticated: !!token };
      notify();
      return data;
    },

    async logout() {
      try {
        await fetch(`${base()}/api/auth/logout`, {
          method: 'POST',
          headers: this.headers(),
          credentials: 'include',
        });
      } catch { /* best-effort */ }
      token = null;
      if (status) status.authenticated = false;
      notify();
    },
  };
})();
