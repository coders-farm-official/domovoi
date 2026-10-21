/* Chat lookups (assistant plan part 5, W1): the permission card, the
 * conversation header's internet chip and the history list's globe.
 *
 * The server decides what a lookup is and whether it may go out
 * (web/backend/api/chat.py). A reply that would leave the house arrives as
 * an assistant row carrying stats.lookup_card { kind, query, destination,
 * state }: this file draws it as a card with the exact words and where they
 * would go, and Allow once / Allow in this chat / Not now. The person's
 * answer goes to POST /api/chat/threads/<id>/lookups/<message id>, which
 * streams the answer like a send. A reply's sources line is part of its
 * text, written by the server from what was fetched, never by the model.
 *
 * The chip reads GET /api/chat/internet (the household's internet answer
 * and its "Always allow internet") and the thread's internet_allowed:
 * "internet off" (Domovoi asks first), "internet on for this chat", or
 * "internet always on". Tapping it turns "on for this chat" on or off.
 * Chats that went online carry a globe in the list (used_internet).
 */

const CHAT_LOOKUP_STATES = {
  allowed_once: 'allowed once',
  allowed_chat: 'allowed in this chat',
  declined: 'not looked up',
};

/* The household's answer, re-read when the page comes back into view. */
const useChatInternet = () => {
  const [info, setInfo] = React.useState(null);
  const read = React.useCallback(() => {
    apiGet('/api/chat/internet', { quiet: true }).then(setInfo).catch(() => setInfo(null));
  }, []);
  React.useEffect(() => {
    read();
    const again = () => { if (document.visibilityState === 'visible') read(); };
    document.addEventListener('visibilitychange', again);
    return () => document.removeEventListener('visibilitychange', again);
  }, [read]);
  return [info, read];
};

/* What the chip says for a thread. */
const chatInternetChip = (info, thread) => {
  if (info && info.internet === 'never') {
    return { label: 'internet off', on: false, fixed: true,
             title: 'This Domovoi stays off the internet (Settings → Internet). Its manual and offline library still answer.' };
  }
  if (info && info.always_allow) {
    return { label: 'internet always on', on: true, fixed: true,
             title: 'Chats look things up without asking (Settings → Chat & AI → Search)' };
  }
  if (thread && thread.internet_allowed) {
    return { label: 'internet on for this chat', on: true, fixed: false,
             title: 'This chat looks things up without asking. Tap to have it ask first again.' };
  }
  return { label: 'internet off', on: false, fixed: false,
           title: 'Domovoi asks before it looks anything up online. Tap to allow it in this chat.' };
};

const ChatInternetChip = ({ info, thread, onToggle }) => {
  if (!thread) return null;
  const c = chatInternetChip(info, thread);
  const press = () => {
    if (c.fixed) {
      try { window.location.hash = info && info.internet === 'never' ? 'settings/internet' : 'settings/search'; }
      catch { /* no location */ }
      return;
    }
    onToggle(!thread.internet_allowed);
  };
  return (
    <button type="button" className={`chat-internet-chip${c.on ? ' on' : ''}`} title={c.title}
            aria-label={`${c.label}. ${c.title}`} onClick={press}>
      <Icon name={c.on ? 'globe' : 'globe-lock'} size={12}/>
      <span>{c.label}</span>
    </button>
  );
};

/* The globe on a chat in the list that has used the internet. */
const ChatInternetMark = ({ t }) => (t && t.used_internet ? (
  <span className="chat-internet-mark" title="this chat looked things up online" aria-label="used the internet">
    <Icon name="globe" size={11}/>
  </span>
) : null);

/* The permission card, from a row's stats.lookup_card. */
const ChatLookupCard = ({ m, busy, onDecide }) => {
  const card = (m && m.stats && m.stats.lookup_card) || null;
  if (!card) return null;
  const page = card.kind === 'page';
  const pending = card.state === 'pending';
  return (
    <div className="chat-lookup-card" role="group" aria-label={page ? 'read this page?' : 'look this up online?'}>
      <div className="chat-lookup-ask">
        <Icon name="globe" size={14}/>
        <span>{page ? 'May I read this page?' : 'May I look this up online?'}</span>
      </div>
      <div className="chat-lookup-query mono">{page ? card.query : `“${card.query}”`}</div>
      <div className="chat-lookup-where">goes to {card.destination}</div>
      {pending ? (
        <div className="chat-lookup-actions">
          <Button variant="primary" disabled={busy} onClick={() => onDecide(m, 'once')}>Allow once</Button>
          <Button disabled={busy} onClick={() => onDecide(m, 'chat')}>Allow in this chat</Button>
          <Button variant="ghost" disabled={busy} onClick={() => onDecide(m, 'no')}>Not now</Button>
        </div>
      ) : (
        <div className="chat-lookup-state mono">{CHAT_LOOKUP_STATES[card.state] || card.state}</div>
      )}
    </div>
  );
};

/* The card's answer with the row marked as answered, before the stream. */
const chatLookupDecided = (m, decision) => {
  const card = (m.stats && m.stats.lookup_card) || {};
  const state = decision === 'no' ? 'declined' : decision === 'chat' ? 'allowed_chat' : 'allowed_once';
  return { ...m, stats: { ...(m.stats || {}), lookup_card: { ...card, state } } };
};

const ChatLookups = {
  useInternet: useChatInternet, chip: chatInternetChip, Chip: ChatInternetChip, Mark: ChatInternetMark,
  Card: ChatLookupCard, decided: chatLookupDecided,
};
window.ChatLookups = ChatLookups;
