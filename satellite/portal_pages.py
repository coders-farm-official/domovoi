"""HTML for the Wi-Fi setup portal — the pages a customer's phone sees.

Rendered by hand, with no template engine and no JavaScript, for two
reasons that both matter here:

* ``provisioning_mode`` and everything it imports must run on a bare image
  with none of the audio stack installed — stdlib only.
* The browser is not a real browser. iOS shows the Captive Network
  Assistant and Android an equivalent mini-browser: restricted JavaScript,
  no service workers, cookies that may not survive, and a window the OS
  can close at any moment. A plain form that works with scripting disabled
  is the only thing guaranteed to function.

Everything interpolated is escaped — the network list is attacker-supplied
(anyone can name an access point ``<script>``) and the error strings can
carry text from a failed join.
"""

from __future__ import annotations

import html
from typing import Any, Iterable

# A short, boring vocabulary covers most of a house. Anything else goes
# through the custom field and comes back slugged.
COMMON_ROOMS = (
    "kitchen",
    "living-room",
    "dining-room",
    "bedroom",
    "office",
    "bathroom",
    "hallway",
    "garage",
    "basement",
    "workshop",
)

_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:28px 20px 56px;background:#fbfaf7;color:#1a1a18;
  font:16px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:26rem;margin:0 auto}
h1{font-size:22px;line-height:1.2;margin:0 0 6px}
p.sub{color:#5d5b55;margin:0 0 24px;font-size:15px}
label{display:block;font-size:13px;letter-spacing:.02em;color:#5d5b55;
  margin:18px 0 6px}
input,select{width:100%;padding:12px;font-size:16px;color:#1a1a18;
  background:#fff;border:1px solid #d9d5cc;border-radius:8px}
input:focus,select:focus{outline:2px solid #e8a33d;outline-offset:1px;
  border-color:#e8a33d}
button{width:100%;margin-top:26px;padding:14px;font-size:16px;font-weight:600;
  color:#3a2c12;background:#e8a33d;border:0;border-radius:8px}
.hint{font-size:13px;color:#77746c;margin-top:6px}
.err{background:#fdeceb;border:1px solid #f0c2be;color:#8c2018;
  padding:12px 14px;border-radius:8px;margin-bottom:20px;font-size:14px}
.code{font:600 32px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;
  letter-spacing:.16em;padding:18px;background:#fff;border:1px solid #d9d5cc;
  border-radius:8px;text-align:center;margin:20px 0}
.done{font-size:15px;color:#5d5b55}
@media(prefers-color-scheme:dark){
  body{background:#141412;color:#f2f0ec}
  p.sub,label,.hint,.done{color:#a6a29a}
  input,select,.code{background:#1f1e1b;border-color:#3a3833;color:#f2f0ec}
  .err{background:#3a1a17;border-color:#7a2f27;color:#f4b8b1}
}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><div class=\"wrap\">{body}</div></body></html>"
    )


def _options(values: Iterable[str], selected: str | None = None) -> str:
    out = []
    for v in values:
        esc = html.escape(v)
        mark = " selected" if v == selected else ""
        out.append(f"<option value=\"{esc}\"{mark}>{esc}</option>")
    return "".join(out)


def render_form(
    *,
    networks: Iterable[str],
    profiles: Iterable[str],
    error: str | None = None,
    room: str | None = None,
    ssid: str | None = None,
) -> str:
    """The setup form. ``networks`` is what the device itself can see, so the
    customer picks their house network from a list instead of typing an SSID
    they may not know the exact spelling of."""
    nets = list(networks)
    profs = list(profiles)

    err_html = f"<div class=\"err\">{html.escape(error)}</div>" if error else ""

    if nets:
        network_field = (
            "<label for=\"ssid\">Your Wi-Fi network</label>"
            f"<select id=\"ssid\" name=\"ssid\">{_options(nets, ssid)}</select>"
        )
    else:
        # A scan can come back empty on a busy radio. Never dead-end.
        network_field = (
            "<label for=\"ssid\">Your Wi-Fi network</label>"
            "<input id=\"ssid\" name=\"ssid\" autocapitalize=\"off\" "
            f"autocorrect=\"off\" value=\"{html.escape(ssid or '')}\" required>"
            "<div class=\"hint\">No networks found in the last scan — "
            "type the name exactly.</div>"
        )

    profile_field = ""
    if len(profs) > 1:
        profile_field = (
            "<label for=\"profile\">Microphone board</label>"
            f"<select id=\"profile\" name=\"profile\">{_options(profs)}</select>"
        )

    body = f"""
{err_html}
<h1>Set up your Domovoi satellite</h1>
<p class="sub">This connects the speaker to your home Wi-Fi. It only takes a moment.</p>
<form method="post" action="/provision">
  {network_field}

  <label for="psk">Wi-Fi password</label>
  <input id="psk" name="psk" type="password" autocapitalize="off"
         autocorrect="off" required>

  <label for="room">Which room is it in?</label>
  <select id="room" name="room">{_options(COMMON_ROOMS, room)}
    <option value="">Something else…</option>
  </select>

  <label for="room_custom">If something else, name it</label>
  <input id="room_custom" name="room_custom" autocapitalize="off"
         autocorrect="off" placeholder="e.g. back porch">
  <div class="hint">Letters, numbers and spaces. Spaces become dashes, and
    everything is lowercased — "Back Porch" becomes "back-porch".</div>
  {profile_field}

  <label for="url">Domovoi server address <span class="hint">(optional)</span></label>
  <input id="url" name="url" autocapitalize="off" autocorrect="off"
         placeholder="leave blank to find it automatically">

  <button type="submit">Connect</button>
</form>
"""
    return _page("Domovoi setup", body)


def render_accepted(*, room_id: str, code: str) -> str:
    """Shown after credentials are accepted.

    This page is the LAST thing the phone can be told: accepting the
    credentials tears down the very network it is reading this over. So it
    promises nothing about the outcome and hands the customer off to the
    dashboard, which is the only place that can actually confirm.
    """
    body = f"""
<h1>Connecting…</h1>
<p class="sub">This speaker is joining your Wi-Fi as
  <strong>{html.escape(room_id)}</strong>. This setup network is closing now,
  so your phone will drop back to your normal Wi-Fi on its own.</p>
<div class="code">{html.escape(code)}</div>
<p class="done">Open your Domovoi dashboard to finish. It will ask you to
  approve a new satellite showing this code. You can close this page.</p>
"""
    return _page("Connecting", body)


def render_probe_redirect(target: str) -> str:
    """Body for the 302 an OS connectivity probe receives. Some clients show
    this rather than following, so it can't be blank."""
    esc = html.escape(target)
    return _page(
        "Domovoi setup",
        f"<h1>Domovoi setup</h1><p class=\"sub\">"
        f"<a href=\"{esc}\">Tap here to set up your satellite</a>.</p>",
    )


def summarize_networks(rows: Iterable[dict[str, Any]]) -> list[str]:
    """Strongest-first, deduplicated SSIDs from a scan. Hidden networks come
    back with an empty SSID and are dropped — they can't be picked from a
    list anyway."""
    best: dict[str, int] = {}
    for row in rows:
        ssid = (row.get("ssid") or "").strip()
        if not ssid:
            continue
        try:
            signal = int(row.get("signal") or 0)
        except (TypeError, ValueError):
            signal = 0
        if ssid not in best or signal > best[ssid]:
            best[ssid] = signal
    return sorted(best, key=lambda s: (-best[s], s.lower()))
