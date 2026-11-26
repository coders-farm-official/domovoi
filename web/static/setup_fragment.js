/* The setup-code QR's address (1.5.0, domovoi/setup_qr.py):
 *
 *   http://192.168.1.20:6369/#setup-code=<the eight words>
 *
 * The code rides in the fragment only, which a browser never sends to the
 * server (nor in a Referer). This runs first, a plain script in <head>
 * before the hash router or anything else reads the address: it takes the
 * code, puts the address back without the fragment (history.replaceState,
 * so neither the address bar nor Back shows it again), and keeps the code
 * in this page's memory only: never in storage, a cookie or a request.
 * The claim form (components.jsx: the first-run welcome and the admin
 * setup dialog) pre-fills its field from SetupFragment.code() and posts it
 * exactly as a typed code (POST /api/auth/setup: same backoff, same checks).
 *
 * Anything else in the fragment (a page route like #music) is left alone. */
(function () {
  var KEY = 'setup-code';
  var MAX = 200;           // an eight-word code is well under this
  var held = null;
  var w = window;
  try {
    var loc = w.location;
    var hash = String((loc && loc.hash) || '');
    var prefix = '#' + KEY + '=';
    if (hash.slice(0, prefix.length) === prefix) {
      var raw = hash.slice(prefix.length);
      var code = null;
      try { code = decodeURIComponent(raw); } catch (e) { code = null; }
      if (code != null) {
        code = code.replace(/^\s+|\s+$/g, '');
        if (code && code.length <= MAX) held = code;
      }
      // Out of the address bar whatever it held: an unreadable code must
      // not sit there either.
      try {
        if (w.history && typeof w.history.replaceState === 'function') {
          w.history.replaceState(w.history.state === undefined ? null : w.history.state, '',
            String(loc.pathname || '/') + String(loc.search || ''));
        } else {
          loc.hash = '';
        }
      } catch (e) {
        try { loc.hash = ''; } catch (e2) { /* nothing more to do */ }
      }
    }
  } catch (e) { held = null; }
  w.SetupFragment = {
    // The code the QR brought, or '' when none did.
    code: function () { return held || ''; },
    has: function () { return !!held; },
    // After a claim: nothing left in memory either.
    forget: function () { held = null; },
  };
})();
