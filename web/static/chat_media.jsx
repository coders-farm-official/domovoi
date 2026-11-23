/* Photo and video results in home-server chats — the dashboard's half of
 * web/backend/api/chat_media.py and domovoi/media_understanding/ask.py
 * (1.5.0, media understanding slice 6).
 *
 * "Show me photos of the beach" is answered by the app with a sentence and
 * up to 12 result cards, kept on the reply as `media` ({state, count, more,
 * items: [{id, kind, name, library_id, path, thumb, taken_at, date_from,
 * might_show, tags, text, at_s}]}). This file draws them under the reply:
 * a grid of thumbnails, each with "Might show: …", its date and, on a
 * video, a time badge for its best moment. A photo opens the picture
 * itself in a new tab (the Files tab's way of opening an image); a video
 * opens the Videos page's player (videos.jsx VideoOverlay) at that moment.
 *
 * Captions, tags and text come from a picture model and from reading the
 * picture: untrusted words, drawn only as text, never markup. Thumbnails
 * ride the household token in the query (withDeviceToken), as every image
 * the chat shows does; only the two thumbnail routes the server names are
 * ever loaded.
 *
 * window.ChatMedia; every top-level name starts ChatMedia: page scripts
 * share one scope.
 */

const ChatMedia = (() => {
  const THUMB_PREFIXES = ['/api/images/thumb?', '/api/videos/poster?'];

  const clock = (s) => {
    const t = Math.max(0, Math.round(Number(s) || 0));
    const h = Math.floor(t / 3600);
    const m = Math.floor((t % 3600) / 60);
    const sec = String(t % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
  };

  const dateText = (item) => {
    const raw = item && (item.taken_at || item.date_from);
    if (!raw) return null;
    const d = new Date(`${String(raw).slice(0, 10)}T12:00:00`);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  };

  const thumbUrl = (item) => {
    const t = String((item && item.thumb) || '');
    return THUMB_PREFIXES.some((p) => t.startsWith(p)) ? withDeviceToken(`${API_BASE}${t}`) : null;
  };

  const rawImageUrl = (item) => withDeviceToken(
    `${API_BASE}/api/images/raw?library_id=${encodeURIComponent(item.library_id)}`
    + `&path=${encodeURIComponent(item.path)}`);

  const hasVideoTime = (item) => item.kind === 'video' && item.at_s != null && Number.isFinite(Number(item.at_s));

  /* What a click does: a photo opens in a new tab; a video plays here. */
  const open = (item, play) => {
    if (item.kind === 'video') {
      play({ video: { library_id: item.library_id, rel: item.path, name: item.name, title: item.name },
             startSec: hasVideoTime(item) ? Number(item.at_s) : null });
      return;
    }
    window.open(rawImageUrl(item), '_blank', 'noopener');
  };

  const Thumb = ({ item }) => {
    const [failed, setFailed] = React.useState(false);
    const src = thumbUrl(item);
    if (!src || failed) {
      return (
        <div className="chat-media-thumb chat-media-thumb-empty">
          <Icon name={item.kind === 'video' ? 'film' : 'image'} size={20}/>
        </div>
      );
    }
    return <img className="chat-media-thumb" src={src} alt="" loading="lazy" onError={() => setFailed(true)}/>;
  };

  const Card = ({ item, onOpen }) => {
    const caption = item.might_show ? `Might show: ${String(item.might_show)}` : null;
    const date = dateText(item);
    const label = item.kind === 'video'
      ? `play ${String(item.name || 'video')}${hasVideoTime(item) ? ` from ${clock(item.at_s)}` : ''}`
      : `open ${String(item.name || 'photo')}`;
    return (
      <button type="button" className="chat-media-card" aria-label={label} title={String(item.name || '')}
              onClick={() => onOpen(item)}>
        <span className="chat-media-frame">
          <Thumb item={item}/>
          {item.kind === 'video' && (
            <span className="mono chat-media-badge">
              {hasVideoTime(item) ? clock(item.at_s) : 'video'}
            </span>
          )}
        </span>
        {caption && <span className="chat-media-caption">{caption}</span>}
        {date && <span className="mono chat-media-date">{date}</span>}
      </button>
    );
  };

  /* The cards under a reply; nothing when there are none. */
  const Cards = ({ media }) => {
    const [playing, setPlaying] = React.useState(null);   // { video, startSec }
    const items = media && Array.isArray(media.items) ? media.items : [];
    if (!items.length) return null;
    const Overlay = typeof VideoOverlay !== 'undefined' ? VideoOverlay : null;
    return (
      <div className="chat-media">
        <div className="chat-media-grid">
          {items.map((it, i) => <Card key={`${it.id}-${i}`} item={it} onOpen={(x) => open(x, setPlaying)}/>)}
        </div>
        {media.more || (media.count || 0) > items.length ? (
          <div className="chat-media-more">
            {media.more ? `more than ${media.count}` : `${media.count}`} found · the rest are on the Photos page
          </div>
        ) : null}
        {playing && Overlay && (
          <Overlay video={playing.video} resumeSec={0} startSec={playing.startSec}
                   onClose={() => setPlaying(null)}/>
        )}
      </div>
    );
  };

  return { Cards, open, clock, dateText, thumbUrl };
})();

window.ChatMedia = ChatMedia;
