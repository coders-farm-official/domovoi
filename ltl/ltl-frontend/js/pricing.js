/* The pricing page. Reads plans from the API rather than hardcoding
 * them, so changing a price is a database row and not a deploy. */

import { api, fmtBytes, fmtMoney, el, sessionToken } from './api.js';

const container = document.getElementById('plans');

function planCard(plan) {
  const price = plan.priceCents === 0
    ? el('div', { class: 'plan-price' }, 'Free')
    : el('div', { class: 'plan-price' },
        fmtMoney(plan.priceCents, plan.currency),
        el('small', {}, ' / month'));

  const cta = el('button', {
    class: plan.priceCents === 0 ? 'btn' : 'btn btn-primary',
    onclick: () => subscribe(plan, cta),
  }, plan.priceCents === 0 ? 'Start free' : 'Choose ' + plan.name);

  return el('div', { class: 'card plan' },
    el('h2', {}, plan.name),
    price,
    el('p', { class: 'subtle' }, plan.description || ''),
    el('ul', {},
      el('li', {}, `${fmtBytes(plan.monthlyBytes)} of relayed data a month`),
      el('li', {}, `${plan.deviceLimit} devices`),
      el('li', {}, `${plan.householdLimit} household${plan.householdLimit === 1 ? '' : 's'}`),
      el('li', {}, 'End-to-end encrypted — we relay, we don’t read')),
    cta);
}

async function subscribe(plan, button) {
  if (!sessionToken()) {
    location.href = 'signin.html?next=' + encodeURIComponent('pricing.html');
    return;
  }
  button.disabled = true;
  button.textContent = 'Starting…';
  try {
    const { url } = await api.checkout(plan.code);
    location.href = url;
  } catch (error) {
    button.disabled = false;
    button.textContent = 'Choose ' + plan.name;
    alert(error.message);
  }
}

(async () => {
  try {
    const plans = await api.plans();
    container.replaceChildren(...plans.map(planCard));
  } catch (error) {
    container.replaceChildren(
      el('div', { class: 'banner err' }, error.message || 'Could not load plans.'));
  }
})();
