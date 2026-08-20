/* The account dashboard: households, devices, usage, billing.
 *
 * Everything on this page is control-plane data — metadata LTL genuinely
 * holds. Nothing here reaches into anybody's house; that is what
 * remote.html does, over a tunnel this page cannot read either.
 */

import { api, fmtBytes, relTime, el, requireSignIn } from './api.js';
import { DeviceStore } from './tunnel.js';

const root = document.getElementById('app');

const banner = (message, kind = 'err') => el('div', { class: 'banner ' + kind }, message);

/* ── households ──────────────────────────────────────────────────────── */

function householdCard(household) {
  return el('div', { class: 'card' },
    el('div', { class: 'card-head' },
      el('h2', {}, household.name),
      el('span', { class: 'pill ' + (household.online ? 'ok' : '') },
        el('span', { class: 'dot' }), household.online ? 'online' : 'offline')),

    el('dl', { class: 'facts' },
      el('dt', {}, 'server'), el('dd', {}, household.hostname || '—'),
      el('dt', {}, 'last seen'), el('dd', {}, relTime(household.lastSeenAt)),
      el('dt', {}, 'agent'), el('dd', {}, household.agentVersion || '—')),

    el('h3', { class: 'subtle', style: 'margin-top:16px' }, 'FINGERPRINT'),
    el('div', { class: 'keyline' }, household.fingerprint),
    el('p', { class: 'subtle' },
      'This must match what your Domovoi dashboard shows. If it ever differs, '
      + 'stop — that is what a substituted key looks like.'),

    el('div', { id: 'usage-' + household.householdId }),

    el('div', { class: 'btn-row', style: 'margin-top:16px' },
      el('a', {
        class: 'btn btn-primary',
        href: `remote.html?household=${encodeURIComponent(household.householdId)}`,
      }, 'Open remotely'),
      el('button', {
        class: 'btn',
        onclick: () => addDevice(household),
      }, 'Add this device'),
      el('button', {
        class: 'btn btn-danger',
        onclick: () => unpair(household),
      }, 'Unpair')),

    el('div', { id: 'devices-' + household.householdId, style: 'margin-top:18px' }));
}

/* Usage renders into its own slot after the shell is up, so a slow
 * aggregate query never delays the page. */
async function loadUsage(household) {
  const container = document.getElementById('usage-' + household.householdId);
  if (!container) return;
  try {
    const usage = await api.usage(household.householdId);
    const percent = usage.bytesLimit
      ? Math.min(100, Math.round((usage.bytesUsed / usage.bytesLimit) * 100))
      : 0;
    container.replaceChildren(
      el('div', { class: 'meter' },
        el('div', {
          class: 'meter-fill',
          style: `width:${percent}%`,
          'data-over': percent >= 90 ? 'true' : 'false',
        })),
      el('div', { class: 'subtle' },
        `${fmtBytes(usage.bytesUsed)} of ${fmtBytes(usage.bytesLimit)} this period`
        + (usage.periodEnd ? ` · resets ${relTime(usage.periodEnd)}` : '')));
  } catch {
    // Usage is informational. A failure here should not put an error
    // banner over a household that is working fine.
    container.replaceChildren();
  }
}

async function loadDevices(household) {
  const container = document.getElementById('devices-' + household.householdId);
  if (!container) return;
  try {
    const devices = await api.devices(household.householdId);
    container.replaceChildren(
      el('h3', { class: 'subtle' }, 'DEVICES'),
      devices.length === 0
        ? el('p', { class: 'subtle' }, 'No devices yet.')
        : el('div', {}, ...devices.map((device) => deviceRow(household, device))));
  } catch (error) {
    container.replaceChildren(banner(error.message));
  }
}

function deviceRow(household, device) {
  const state = device.revoked ? ['err', 'revoked']
    : device.approved ? ['ok', 'approved']
    : ['warn', 'waiting for approval'];

  return el('div', { class: 'row' },
    el('div', { class: 'row-main' },
      el('div', { class: 'row-title' }, device.label),
      el('div', { class: 'row-meta mono' }, device.fingerprint),
      el('div', { class: 'row-meta' },
        `${device.platform || 'device'} · seen ${relTime(device.lastSeenAt)}`
        + (device.lastSeenCountry ? ` · ${device.lastSeenCountry}` : ''))),
    el('div', { class: 'row-actions' },
      el('span', { class: 'pill ' + state[0] }, state[1]),
      device.revoked ? null : el('button', {
        class: 'btn btn-danger',
        onclick: async () => {
          if (!confirm(`Revoke ${device.label} at the relay?\n\n`
            + 'This stops it connecting. To fully remove its access, also revoke it '
            + 'on your Domovoi dashboard — that is the side that decides.')) return;
          await api.revokeDevice(device.deviceId);
          loadDevices(household);
        },
      }, 'Revoke')));
}

/* ── this device ─────────────────────────────────────────────────────── */
/*
 * Registering generates a keypair whose private half is non-extractable
 * and never leaves this browser. What goes to LTL is the public half —
 * and even that grants nothing until a human approves it on the Domovoi
 * dashboard.
 */
async function addDevice(household) {
  const label = prompt('Name this device', deviceGuess());
  if (!label) return;
  try {
    const publicKey = await DeviceStore.publicKeyB64();
    const result = await api.registerDevice({
      householdId: household.householdId,
      label,
      publicKey,
      platform: navigator.platform || 'browser',
    });
    await DeviceStore.setDeviceId(result.device.deviceId);
    alert(`${label} is registered.\n\n${result.nextStep}\n\n`
      + `Fingerprint to check on the dashboard:\n${result.device.fingerprint}`);
    loadDevices(household);
  } catch (error) {
    alert(error.message);
  }
}

function deviceGuess() {
  const agent = navigator.userAgent;
  if (/iPhone|iPad/.test(agent)) return 'iPhone';
  if (/Android/.test(agent)) return 'Android phone';
  if (/Mac/.test(agent)) return 'Mac';
  if (/Windows/.test(agent)) return 'PC';
  return 'Browser';
}

async function unpair(household) {
  if (!confirm(`Unpair ${household.name}?\n\n`
    + 'Remote access stops and every device has to be approved again. '
    + 'Your Domovoi server itself is untouched and keeps working on your home network.')) return;
  await api.unpair(household.householdId);
  render();
}

/* ── pairing ─────────────────────────────────────────────────────────── */

function pairCard() {
  const input = el('input', {
    type: 'text',
    id: 'pair-code',
    placeholder: 'maple heron brick oak fern dawn owl river',
    autocomplete: 'off',
    autocapitalize: 'off',
    spellcheck: 'false',
  });
  const status = el('div', { class: 'hidden' });
  const button = el('button', { class: 'btn btn-primary' }, 'Pair this server');

  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      await api.claim(input.value, null);
      render();
    } catch (error) {
      status.className = 'banner err';
      status.textContent = error.message;
      button.disabled = false;
    }
  });

  return el('div', { class: 'card' },
    el('h2', {}, 'Add a household'),
    el('p', { class: 'muted' },
      'On your Domovoi dashboard, open Remote Access and press '
      + '“Get a pairing code”. Type the eight words here.'),
    el('p', { class: 'subtle' },
      'Only a hash of the code reaches us, so the words themselves never leave your house.'),
    status,
    el('label', { for: 'pair-code' }, 'Pairing code'),
    input,
    button);
}

/* ── billing ─────────────────────────────────────────────────────────── */

function billingCard(subscription) {
  const manage = el('button', { class: 'btn' }, 'Manage billing');
  manage.addEventListener('click', async () => {
    manage.disabled = true;
    try {
      const { url } = await api.portal();
      location.href = url;
    } catch (error) {
      manage.disabled = false;
      alert(error.message);
    }
  });

  if (!subscription) {
    return el('div', { class: 'card' },
      el('h2', {}, 'Plan'),
      el('p', { class: 'muted' }, 'You’re on the free tier.'),
      el('a', { class: 'btn btn-primary', href: 'pricing.html' }, 'See plans'));
  }

  const tone = { active: 'ok', trialing: 'ok', past_due: 'warn', canceled: 'err' };
  return el('div', { class: 'card' },
    el('div', { class: 'card-head' },
      el('h2', {}, subscription.plan.name),
      el('span', { class: 'pill ' + (tone[subscription.status] || '') }, subscription.status)),
    subscription.graceUntil
      ? banner(`A payment didn’t go through. Remote access keeps working until `
        + `${relTime(subscription.graceUntil)} — update your card to avoid interruption.`, 'warn')
      : null,
    subscription.cancelAtPeriodEnd
      ? banner(`Cancels ${relTime(subscription.currentPeriodEnd)}.`, 'warn')
      : null,
    el('dl', { class: 'facts' },
      el('dt', {}, 'data'), el('dd', {}, `${fmtBytes(subscription.plan.monthlyBytes)} a month`),
      el('dt', {}, 'devices'), el('dd', {}, String(subscription.plan.deviceLimit)),
      el('dt', {}, 'renews'), el('dd', {}, relTime(subscription.currentPeriodEnd))),
    el('div', { class: 'btn-row', style: 'margin-top:14px' },
      manage,
      el('a', { class: 'btn', href: 'pricing.html' }, 'Change plan')));
}

/* ── page ────────────────────────────────────────────────────────────── */

async function render() {
  root.replaceChildren(el('p', { class: 'muted' }, 'Loading…'));
  try {
    const account = await api.account();
    const nodes = [
      el('h1', {}, 'Your account'),
      el('p', { class: 'lede' }, account.email),
      billingCard(account.subscription),
    ];

    if (account.households.length === 0) {
      nodes.push(pairCard());
    } else {
      for (const household of account.households) {
        nodes.push(householdCard(household));
      }
      nodes.push(pairCard());
    }

    root.replaceChildren(...nodes);

    // Usage and devices load after the shell so a slow query never
    // blocks the page from appearing.
    for (const household of account.households) {
      loadDevices(household);
      loadUsage(household);
    }
  } catch (error) {
    if (error.code === 'UNAUTHENTICATED') {
      location.href = 'signin.html?next=' + encodeURIComponent('app.html');
      return;
    }
    root.replaceChildren(banner(error.message || 'Something went wrong.'));
  }
}

if (requireSignIn()) render();
