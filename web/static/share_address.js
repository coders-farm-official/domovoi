/* The address to share: what the dashboard's header shows as "where this
 * Domovoi is", for a person to read out or send to someone else. Display
 * and sharing only: the page keeps talking to the server the way it always
 * did (data.js API_BASE / same origin).
 *
 * The easiest one that works, in this order:
 *   1. the network name with no port ("domovoi.local"), when the short
 *      address is on (GET /api/short-address: port 80 hands browsers to
 *      the dashboard) and the name is announced (GET /api/lan-name);
 *   2. the name with the dashboard's port ("domovoi.local:6369"), when the
 *      name works but the short address is off;
 *   3. the server's IP with the dashboard's port ("192.168.1.20:6369"),
 *      when no name is announced.
 * null when none can be told (an unpaired browser reads neither): the
 * header then shows what it always showed, the address this page uses.
 *
 * String in / object out with no DOM, so
 * domovoi/tests/test_web_share_address.py runs the real thing under node.
 */

const ShareAddress = (() => {
  // GET /api/lan-name states in which the name held answers on the network
  // (domovoi/lan_name/service.py claimed_name: the same set).
  const NAME_WORKS = ['announced', 'held', 'taken', 'checking'];
  const DASHBOARD_PORT = 6369;

  const bare = (host) => String(host || '').replace(/^\[|\]$/g, '').toLowerCase();
  const isIpv4 = (h) => /^\d{1,3}(\.\d{1,3}){3}$/.test(h);
  const isIp = (h) => isIpv4(h) || (h.includes(':') && /^[0-9a-f:.]+$/.test(h));
  const isLoopback = (h) => h === '::1' || /^127\./.test(h) || h === 'localhost';
  const hostPart = (ip) => (ip.includes(':') ? `[${ip}]` : ip);

  // lan: the /api/lan-name answer or null; short: the /api/short-address
  // answer or null; here: { hostname } of the server this page talks to.
  const pick = (lan, short, here) => {
    const port = (short && Number(short.dashboard_port)) || DASHBOARD_PORT;
    const name = lan && lan.claimed && NAME_WORKS.includes(lan.state)
      ? String(lan.fqdn || `${lan.claimed}.local`).toLowerCase() : null;
    if (name) {
      if (short && short.state === 'on') return { kind: 'short', text: name, url: `http://${name}` };
      return { kind: 'name', text: `${name}:${port}`, url: `http://${name}:${port}` };
    }
    let ip = null;
    const h = bare(here && here.hostname);
    if (h && isIp(h) && !isLoopback(h)) ip = h;
    else if (lan && Array.isArray(lan.addresses)) {
      ip = lan.addresses.map(bare).find((a) => isIp(a) && !isLoopback(a)) || null;
    }
    if (!ip) return null;
    const text = `${hostPart(ip)}:${port}`;
    return { kind: 'ip', text, url: `http://${text}` };
  };

  return { pick, NAME_WORKS, DASHBOARD_PORT };
})();

if (typeof window !== 'undefined') window.ShareAddress = ShareAddress;
