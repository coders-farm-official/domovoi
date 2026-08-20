/* The remote console — the page that actually talks to your house.
 *
 * It opens a tunnel (js/tunnel.js), shows the fingerprint it negotiated
 * so it can be compared against the dashboard at home, and then issues
 * real requests to the Domovoi dashboard API through it. Everything
 * rendered below the fingerprint arrived encrypted end to end; the relay
 * that carried it could not read any of it.
 *
 * On the scope of this page: it is a working console over the tunnel,
 * not a mirror of the whole Domovoi dashboard. Mirroring the dashboard
 * needs a Service Worker to intercept its relative fetches and route
 * them through here — see the note in ../README.md. The tunnel below is
 * the part that has to be right first, and it is the same tunnel that
 * work would use.
 */

import { api, el, requireSignIn } from './api.js';
import { Tunnel, TunnelError, DeviceStore, closeReasonText } from './tunnel.js';

const root = document.getElementById('remote');
const householdId = new URLSearchParams(location.search).get('household');

const RELAY_URL = window.LTL_RELAY_URL
  || (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/relay/v1/client';

let tunnel = null;
const log = el('div', { class: 'console' });

function say(text, kind = '') {
  log.append(el('div', { class: kind }, text));
  log.scrollTop = log.scrollHeight;
}

/* ── connecting ──────────────────────────────────────────────────────── */

async function connect(household) {
  const deviceId = await DeviceStore.deviceId();
  if (!deviceId) {
    return fail('This browser isn’t registered as a device yet. '
      + 'Go back to your account and press “Add this device”.');
  }

  say(`connecting to ${household.name}…`, 'dim');
  try {
    tunnel = await Tunnel.connect({
      relayUrl: RELAY_URL,
      householdId: household.householdId,
      deviceId,
      householdDhKey: household.dhPublicKey,
      onPinMismatch: (pinned, presented) => {
        say('PINNED KEY DOES NOT MATCH', 'err');
        say('pinned:    ' + pinned, 'err');
        say('presented: ' + presented, 'err');
      },
    });
  } catch (error) {
    return fail(explain(error));
  }

  tunnel.onClose = (reason) => {
    say('link closed: ' + closeReasonText(reason), 'err');
  };

  say('connected — session keys established', 'ok');
  say('fingerprint: ' + tunnel.fingerprint);
  document.getElementById('fp').textContent = tunnel.fingerprint;
  document.getElementById('fp-card').classList.remove('hidden');
  document.getElementById('actions').classList.remove('hidden');
}

function explain(error) {
  if (!(error instanceof TunnelError)) return error.message;
  switch (error.code) {
    case 'KEY_CHANGED':
      return 'This house is presenting a different key than this device pinned. '
        + 'Do not continue. Either the server’s keys were regenerated — in which case '
        + 'unpair and pair again — or something is impersonating it.';
    case 'DEVICE_NOT_APPROVED':
      return 'Your Domovoi dashboard hasn’t approved this device yet. '
        + 'Open Remote Access there, check the fingerprint, and approve it.';
    default:
      return error.message;
  }
}

function fail(message) {
  say(message, 'err');
  document.getElementById('error').className = 'banner err';
  document.getElementById('error').textContent = message;
}

/* ── doing things through the tunnel ─────────────────────────────────── */

async function call(label, method, path, body) {
  if (!tunnel) return;
  say(`${method} ${path}`, 'dim');
  const started = performance.now();
  try {
    const { status, data } = await tunnel.json(method, path, body);
    const ms = Math.round(performance.now() - started);
    say(`  ${status} in ${ms}ms`, status < 400 ? 'ok' : 'err');
    render(label, data);
  } catch (error) {
    say('  ' + (error.message || 'failed'), 'err');
  }
}

function render(label, data) {
  const output = document.getElementById('output');
  output.replaceChildren(
    el('h3', { class: 'subtle' }, label.toUpperCase()),
    el('pre', { class: 'console' }, JSON.stringify(data, null, 2)));
}

/* ── page ────────────────────────────────────────────────────────────── */

async function start() {
  if (!householdId) return fail('No household selected.');
  try {
    const households = await api.households();
    const household = households.find((h) => h.householdId === householdId);
    if (!household) return fail('That household isn’t on your account.');

    document.getElementById('title').textContent = household.name;
    if (!household.online) {
      return fail('Your Domovoi server isn’t connected right now. '
        + 'It reconnects on its own — try again in a moment.');
    }
    await connect(household);
  } catch (error) {
    fail(error.message || 'Could not load your households.');
  }
}

document.getElementById('log-slot').append(log);

document.getElementById('btn-satellites')
  .addEventListener('click', () => call('Satellites', 'GET', '/api/satellites'));
document.getElementById('btn-now-playing')
  .addEventListener('click', () => call('Now playing', 'GET', '/api/now-playing'));
document.getElementById('btn-plugins')
  .addEventListener('click', () => call('Plugins', 'GET', '/api/plugins'));
document.getElementById('btn-blocked')
  .addEventListener('click', () => call('Refused', 'GET', '/etc/passwd'));
document.getElementById('btn-disconnect')
  .addEventListener('click', () => { if (tunnel) tunnel.close(); });

if (requireSignIn()) start();
