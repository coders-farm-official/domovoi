/* What =IMAGE(...) may load in the sheet editor (security review round 1,
 * 2026-10-26). Univer's IMAGE draws any URL a cell names, which would have
 * the household's browsers fetch pictures from anywhere on the internet
 * whenever a sheet is opened: a third party learns who opened it, and when.
 * Domovoi makes no outside requests, so IMAGE takes only a picture inside
 * the formula (data:image/png|jpeg|gif|webp;base64) or one that RESOLVES to
 * this page's own origin over http(s). Anything else is #VALUE!, and nothing
 * is fetched. */
export function imageUrlAllowed(url, origin, base) {
  if (typeof url !== 'string' || !url) return false;
  // A picture inside the formula: the ORIGINAL string, strictly.
  if (/^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$/.test(url)) return true;
  // Everything else: resolve it exactly as the browser will (which strips
  // tabs and control characters and reads "/\host" as "//host"), then
  // allow only this page's own origin over http(s).
  let r;
  try {
    r = new URL(url, base || origin);
  } catch (e) {
    return false;
  }
  return (r.protocol === 'http:' || r.protocol === 'https:') && r.origin === origin;
}

/* A subclass of Univer's own IMAGE function that refuses other URLs. */
export function makeSafeImage(BaseImage, ErrorValueObject, getOrigin, getBase = getOrigin) {
  return class SafeImageFunction extends BaseImage {
    _handleSingleObject(source, ...rest) {
      if (source && typeof source.isString === 'function' && source.isString()) {
        if (!imageUrlAllowed(String(source.getValue()), getOrigin(), getBase())) {
          return ErrorValueObject.create('#VALUE!');
        }
      }
      return super._handleSingleObject(source, ...rest);
    }
  };
}
