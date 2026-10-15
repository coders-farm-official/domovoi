/* Chat message timestamps and the "details" rows — the dashboard's copy of
 * the Android app's ChatMessageActions.kt, worded the same so the two read
 * alike. String in / string out with no DOM, so
 * domovoi/tests/test_web_chat_details.py runs the real thing under node.
 *
 * A reply's `stats` is chat_messages.stats (V017); every key is optional.
 */

const ChatDetails = (() => {
  // en-US: "Sep", "Wed" — en-GB now says "Sept" on newer ICU.
  const EN = 'en-US';
  const DAY = 86400000;
  // Below this, "loading the model" is Ollama's bookkeeping, not a cold start.
  const COLD_LOAD_MS = 250;

  const parse = (iso) => {
    if (!iso) return null;
    const t = new Date(iso);
    return Number.isNaN(t.getTime()) ? null : t;
  };
  const dayStart = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const hm = (d) => `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
  const fmt = (d, opts) => new Intl.DateTimeFormat(EN, opts).format(d);
  const n = (x) => Number(x).toLocaleString('en-US');
  const plural = (k, one, many) => `${n(k)} ${k === 1 ? one : many}`;

  /* "12:37" today, "yesterday 12:37", "wed 12:37" this week,
   * "3 sep 12:37" this year, else "24 dec 2025 18:00". */
  const stamp = (iso, now = new Date()) => {
    const t = parse(iso);
    if (!t) return null;
    const days = Math.round((dayStart(now) - dayStart(t)) / DAY);
    let out;
    if (days === 0) out = hm(t);
    else if (days === 1) out = `yesterday ${hm(t)}`;
    else if (days >= 2 && days <= 6) out = `${fmt(t, { weekday: 'short' })} ${hm(t)}`;
    else if (t.getFullYear() === now.getFullYear()) out = `${t.getDate()} ${fmt(t, { month: 'short' })} ${hm(t)}`;
    else out = `${t.getDate()} ${fmt(t, { month: 'short' })} ${t.getFullYear()} ${hm(t)}`;
    return out.toLowerCase();
  };

  const secs = (ms) => (ms < 10000
    ? `${(Math.round(ms / 100) / 10).toFixed(1)}s`
    : `${Math.round(ms / 1000)}s`);

  const rel = (t, now) => {
    const sec = Math.round((now - t) / 1000);
    if (sec < 45) return 'just now';
    if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
    if (sec < 86400 * 30) return `${Math.round(sec / 86400)}d ago`;
    return fmt(t, { day: 'numeric', month: 'short', year: 'numeric' });
  };

  const lengthLine = (text) => {
    const s = String(text || '');
    const words = s.split(/\s+/).filter(Boolean).length;
    const lines = s === '' ? 0 : s.replace(/\s+$/, '').split('\n').length;
    return [plural(s.length, 'character', 'characters'), plural(words, 'word', 'words'),
            plural(lines, 'line', 'lines')].join(' · ');
  };

  const contextLine = (st) => {
    if (st.context_sent == null) return null;
    const sent = st.context_sent;
    const inThread = st.context_in_thread;
    const base = inThread != null && inThread > sent
      ? `saw the last ${sent} of ${inThread} messages; older ones were left out`
      : `saw all ${sent} ${sent === 1 ? 'message' : 'messages'} in the thread`;
    return base + (st.num_ctx ? ` · ${n(st.num_ctx)}-token window` : '');
  };

  /* [[label, value], ...] for a message row as the API returns it, plus
   * `device` (the display name to show for a user message). */
  const rows = (m, threadId, now = new Date()) => {
    const out = [];
    const isUser = m.role === 'user';
    const st = m.stats || null;
    out.push(['from', isUser ? 'you' : 'domovoi']);
    const t = parse(m.created_at);
    if (t) {
      const full = `${fmt(t, { weekday: 'short' })} ${t.getDate()} ${fmt(t, { month: 'short' })} `
        + `${t.getFullYear()}, ${hm(t)}:${String(t.getSeconds()).padStart(2, '0')}`;
      out.push(['sent', `${full} (${rel(t, now)})`]);
    }
    if (isUser && m.device) out.push(['device', m.device]);
    if (!isUser && m.model) {
      const why = { chat: ' (chat model)', vision: ' (vision model: images attached)',
                    override: ' (chosen for this message)' }[st && st.model_role] || '';
      out.push(['model', m.model + why]);
    }
    if (st) {
      const reply = [
        st.first_token_ms != null && `first words after ${secs(st.first_token_ms)}`,
        st.wall_ms != null && `finished in ${secs(st.wall_ms)}`,
      ].filter(Boolean);
      if (reply.length) out.push(['reply time', reply.join(' · ')]);
      const spent = [
        st.load_ms != null && st.load_ms >= COLD_LOAD_MS && `loading the model ${secs(st.load_ms)}`,
        st.prompt_ms != null && `reading ${secs(st.prompt_ms)}`,
        st.generate_ms != null && `writing ${secs(st.generate_ms)}`,
      ].filter(Boolean);
      if (spent.length) out.push(['time spent', spent.join(' · ')]);
      const tokens = [
        st.prompt_tokens != null && `${n(st.prompt_tokens)} in`,
        st.output_tokens != null && `${n(st.output_tokens)} out`,
      ].filter(Boolean);
      if (tokens.length) out.push(['tokens', tokens.join(' · ')]);
      if (st.tokens_per_sec != null) out.push(['speed', `${Number(st.tokens_per_sec).toFixed(1)} tokens/s`]);
      if (st.done_reason) {
        out.push(['stopped', { stop: 'finished normally', length: 'cut off at the length limit' }[st.done_reason]
          || st.done_reason]);
      }
      const ctx = contextLine(st);
      if (ctx) out.push(['context', ctx]);
    }
    out.push(['length', lengthLine(m.content)]);
    const images = (m.images || []).map((i) => i.name || 'image');
    if (images.length) out.push(['images', `${images.length} · ${images.join(', ')}`]);
    if (m.error) out.push(['error', m.error]);
    out.push(['message', typeof m.id === 'number'
      ? `#${m.id} in thread #${threadId}` : `thread #${threadId} (not saved yet)`]);
    return out;
  };

  /* ── pictures, and the plain answers for a reply that failed ──────────
   * Worded like web/backend/api/chat.py (vision_missing_text) and the
   * Android app's ChatErrors.kt. */

  // What the paperclip takes (the upload's own allowlist, chat.py).
  const ACCEPTS = 'Chat takes text and pictures: PNG, JPEG, GIF, WebP or BMP, '
    + 'up to 4 a message. No other files.';

  const visionMissingText = (model) => (String(model || '').trim()
    ? `Pictures need the vision model ${model}, which isn't installed on this Domovoi. `
      + 'An admin can install it in Settings → Models, or pick another vision model there.'
    : 'Pictures need a vision model, and none is set on this Domovoi. '
      + 'An admin can pick one in Settings → Models.');

  /* Is a picture sure to be refused? Only when the server said so
   * (/api/chat/models `vision_installed: false`); not knowing — Ollama
   * down, the read not back yet — leaves it to the send. */
  const visionMissing = (info) => !!info && info.vision_installed === false;

  // Codes whose answer is a model to install or pick: an admin's job,
  // in Settings → Models.
  const MODEL_CODES = ['vision_model_missing', 'model_missing', 'vision_unsupported', 'model_memory'];

  /* How a reply's error reads: {text, code, models} — `models` true when
   * Settings → Models is where it gets fixed. Null when there is none.
   * The code is the row's `stats.error_code` (or a live stream's). */
  const errorView = (m) => {
    if (!m || !m.error) return null;
    const code = (m.stats && m.stats.error_code) || m.error_code || null;
    const text = code === 'vision_model_missing' ? visionMissingText(m.model || '') : String(m.error);
    return { text, code, models: MODEL_CODES.includes(code) };
  };

  /* Who Domovoi heard on a message said into the app's chat mic (V025),
   * worded as the app's SpeakerMarker.kt: the name at full strength for a
   * clear match, "maybe <name>" drawn softer for a weak one, "voice not
   * recognized" for nobody. Null for anything typed and for replies. The
   * app also softens a match that isn't its owner; a browser has none. */
  const speakerMark = (m) => {
    if (!m || m.source !== 'voice' || m.role !== 'user') return null;
    if (m.speaker_person_id == null) {
      return { label: 'voice not recognized', name: null, soft: true,
               description: "spoken; Domovoi didn't recognize the voice" };
    }
    const who = (m.speaker_name || '').trim() || 'someone it knows';
    if (!m.speaker_confident) {
      return { label: `maybe ${who}`, name: who, soft: true,
               description: `spoken; Domovoi thinks it was ${who}, but isn't sure` };
    }
    return { label: who, name: who, soft: false, description: `spoken by ${who}` };
  };

  return { stamp, rows, lengthLine, ACCEPTS, visionMissingText, visionMissing, errorView, speakerMark };
})();

window.ChatDetails = ChatDetails;
