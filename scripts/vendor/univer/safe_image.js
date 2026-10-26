/* What =IMAGE(...) may load in the sheet editor (security review round 1,
 * 2026-10-26). Univer's IMAGE draws any URL a cell names, which would have
 * the household's browsers fetch pictures from anywhere on the internet
 * whenever a sheet is opened: a third party learns who opened it, and when.
 * Domovoi makes no outside requests, so IMAGE takes only a picture inside
 * the formula (data:image/...) or one on this server (a relative URL or the
 * page's own origin). Anything else is #VALUE!, and nothing is fetched. */
export function imageUrlAllowed(url, origin) {
  if (typeof url !== 'string') return false;
  const u = url.trim();
  if (!u) return false;
  if (/^data:image\/(png|jpe?g|gif|webp|bmp);/i.test(u)) return true;
  if (/^[a-z][a-z0-9+.-]*:/i.test(u) || u.startsWith('//') || u.startsWith('\\')) {
    // An absolute URL (or scheme-relative): only this page's own origin.
    try {
      return new URL(u).origin === origin && /^https?:$/i.test(new URL(u).protocol);
    } catch (e) {
      return false;
    }
  }
  return true; // relative to this server
}

/* A subclass of Univer's own IMAGE function that refuses other URLs. */
export function makeSafeImage(BaseImage, ErrorValueObject, getOrigin) {
  return class SafeImageFunction extends BaseImage {
    _handleSingleObject(source, ...rest) {
      if (source && typeof source.isString === 'function' && source.isString()) {
        if (!imageUrlAllowed(String(source.getValue()), getOrigin())) {
          return ErrorValueObject.create('#VALUE!');
        }
      }
      return super._handleSingleObject(source, ...rest);
    }
  };
}
