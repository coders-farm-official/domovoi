/* Sign-in.
 *
 * Stytch owns the credential flow; this page's only job is to hand the
 * resulting session JWT to the API client and provision the local
 * account. Nothing here validates a password, because nothing here
 * should ever see one.
 */

import { api, setSessionToken, el } from './api.js';

const form = document.getElementById('signin-form');
const status = document.getElementById('status');
const tokenField = document.getElementById('token');

function say(message, kind = '') {
  status.className = 'banner ' + kind;
  status.textContent = message;
  status.classList.remove('hidden');
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const token = tokenField.value.trim();
  if (!token) return;

  setSessionToken(token);
  try {
    await api.bootstrap();
    const next = new URLSearchParams(location.search).get('next') || 'app.html';
    location.href = next;
  } catch (error) {
    setSessionToken(null);
    say(error.message || 'That session could not be verified.', 'err');
  }
});

/* A Stytch magic link lands back here with the session JWT in the
 * fragment. Reading it from the fragment rather than the query string
 * keeps it out of server logs and out of the Referer header. */
const fragment = new URLSearchParams(location.hash.slice(1));
const fromLink = fragment.get('stytch_session_jwt');
if (fromLink) {
  tokenField.value = fromLink;
  history.replaceState(null, '', location.pathname + location.search);
  form.requestSubmit();
}
