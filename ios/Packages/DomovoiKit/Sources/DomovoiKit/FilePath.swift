import Foundation

/// Every `/api/files` route this screen asks for, and every body it sends,
/// escaped once, here.
///
/// `net/FilesIo.kt` builds these inline with `Uri.encode`. They are a type
/// for ``VideoPath``'s reason, which is sharper here than anywhere else in
/// the app: a library id and a **relative path** both travel as query values,
/// a path holds `/` by definition and can hold a space, an `&`, a `#`, a `+`
/// or a Windows backslash, and a screen assembling that string by hand gets
/// it wrong once and then reads, moves or saves a different file than the one
/// on screen.
public enum FilePath {

    /// The same escaping ``VideoPath/esc(_:)`` does, and **the same
    /// function** rather than a second copy: unreserved characters only, so
    /// `&`, `=`, `#`, `+`, `/`, `\` and a space are all escaped. Two copies
    /// of an escaping rule is how two screens come to disagree about a
    /// filename with a plus in it.
    static func esc(_ raw: String) -> String { VideoPath.esc(raw) }

    /// The same, for a value that travels as **path segments** rather than a
    /// query value, where the separators have to survive: `/api/documents/text/
    /// {rel_path:path}` takes the whole relative path as the tail of the URL.
    ///
    /// So `/` is allowed through and everything else is escaped — including
    /// `?` and `#`, which would otherwise end the path and turn the rest of a
    /// filename into a query string.
    static func escPath(_ raw: String) -> String {
        let allowed = CharacterSet(charactersIn: "-._~/").union(.alphanumerics)
        return raw.addingPercentEncoding(withAllowedCharacters: allowed) ?? raw
    }

    // MARK: - reads

    public static let libraries = "/api/files/libraries"

    /// One directory level, with the name filter and the folders-only
    /// narrowing. **No paging** — the pager adds that (``Paging/path(_:_:)``).
    ///
    /// - Parameters:
    ///   - deviceID: only affects the answer's `writable` / `blocked_reason`;
    ///     reading is never blocked. Left out when there is none to send, and
    ///     when it is **shorter than three characters**, because the server
    ///     types it `min_length=3` (`files.py:683`) and a 422 on the browse
    ///     would take the whole listing down over a field that only decides
    ///     whether two write buttons are drawn.
    ///   - filter: a case-insensitive substring of the entry name, applied by
    ///     the server to the whole folder rather than to the page. Blank is
    ///     left out rather than sent as `q=`.
    ///   - foldersOnly: `kind=folder`, which is what the move destination
    ///     picker lists.
    public static func browse(
        libraryID: String,
        path: String = "",
        deviceID: String? = nil,
        filter: String = "",
        foldersOnly: Bool = false
    ) -> String {
        var out = "/api/files/browse?library_id=\(esc(libraryID))&path=\(esc(path))"
        if let deviceID, deviceID.count >= 3 { out += "&device_id=\(esc(deviceID))" }
        let needle = filter.trimmingCharacters(in: .whitespacesAndNewlines)
        if !needle.isEmpty { out += "&q=\(esc(needle))" }
        if foldersOnly { out += "&kind=folder" }
        return out
    }

    /// A file's bytes, as an attachment.
    ///
    /// Fetched by the app through its own client, which carries the household
    /// token in a header — **never handed to another app as an address**. A
    /// browser given this URL has no token to send and is told
    /// "X-Device-Token or admin session required", and a URL that did carry
    /// one would be the token leaving the app. The one route in this app that
    /// puts a token in a URL is ``VideoStream``, and it says at length why it
    /// has to.
    public static func download(libraryID: String, rel: String) -> String {
        "/api/files/download?library_id=\(esc(libraryID))&path=\(esc(rel))"
    }

    /// The original image in any library, served inline.
    ///
    /// Note what the server does with an **SVG**: it comes back as a
    /// download with inert headers rather than inline, because an SVG is a
    /// document a browser executes and not a picture it decodes
    /// (`web/backend/api/images.py`). Nothing on this side needs to know —
    /// the bytes arrive either way — but the viewer cannot decode them, which
    /// is why it has a "not a picture this phone can show" state rather than
    /// a spinner that never ends.
    public static func imageRaw(libraryID: String, rel: String) -> String {
        "/api/images/raw?library_id=\(esc(libraryID))&path=\(esc(rel))"
    }

    /// A Documents file read as UTF-8 text.
    ///
    /// **Documents only**, and not by convention: the route resolves
    /// `rel_path` inside the household's documents folder
    /// (`documents.py`'s `_readable_target`), so the same filename in the
    /// Music library is a 404 here. ``FileKinds/action(for:in:saveCap:)``
    /// offers ``FileKinds/FileAction/read`` for that library alone.
    ///
    /// A file over the server's ceiling, or one that is not valid UTF-8,
    /// answers **415** with `{"reason": "too_large" | "binary"}` rather than
    /// failing — which is why the text load has those two states.
    public static func documentText(rel: String) -> String {
        "/api/documents/text/\(escPath(rel))"
    }

    // MARK: - writes

    public static let move = "/api/files/move"
    public static let importFile = "/api/files/import"

    /// Where a delete would go, if this app could make one.
    ///
    /// It is **not called anywhere**, and that is the point of it being
    /// written down: `/api/files/delete` is the one Files verb behind
    /// `require_admin_mutation` (`files.py:1468`), this app has no admin
    /// session and cannot get one — the dashboard's admin cookie is
    /// explicitly refused on this client (``DomovoiClient``) — so a delete
    /// from a phone answers 401 every single time.
    ///
    /// Android draws `Delete…` anyway, with a confirmation dialog, and then
    /// says "delete needs admin — use the dashboard, or ask an admin" when it
    /// fails (`FilesScreen.kt:430`). That is a control which cannot do what
    /// it says, and the owner's standing rule is that those are not drawn
    /// ([[no-misleading-controls]]), so this screen does not draw one. The
    /// route is here so that the next reader looking for "where is delete"
    /// finds the answer and not a gap.
    public static let deleteNeedsAnAdmin = "/api/files/delete"

    /// A move's body: one path at a time from this screen, into a library and
    /// a folder.
    ///
    /// `device_id` is **required** on every open write (`files.py:321`), and
    /// not because it proves anything — it is self-asserted — but because a
    /// blocklist anyone evades by leaving the field out is far easier to slip
    /// than one that needs somebody else's id. A phone with no device id
    /// cannot write, and ``FilesModel`` says so rather than sending a body
    /// the server would answer 422 to.
    public nonisolated static func moveBody(
        libraryID: String,
        rel: String,
        targetLibraryID: String,
        targetPath: String,
        deviceID: String
    ) -> Data {
        var out = #"{"source_library_id":"# + VideoPath.quoted(libraryID)
        out += #","paths":["# + VideoPath.quoted(rel) + "]"
        out += #","target_library_id":"# + VideoPath.quoted(targetLibraryID)
        out += #","target_path":"# + VideoPath.quoted(targetPath)
        out += #","device_id":"# + VideoPath.quoted(deviceID)
        out += "}"
        return Data(out.utf8)
    }

    /// An import's body: copy one file or folder off a removable drive into
    /// an importable library, **server-side** — the phone never carries the
    /// bytes.
    public nonisolated static func importBody(
        sourceLibraryID: String,
        sourceRel: String,
        targetLibraryID: String,
        targetPath: String = "",
        deviceID: String
    ) -> Data {
        var out = #"{"source_library_id":"# + VideoPath.quoted(sourceLibraryID)
        out += #","source_path":"# + VideoPath.quoted(sourceRel)
        out += #","target_library_id":"# + VideoPath.quoted(targetLibraryID)
        out += #","target_path":"# + VideoPath.quoted(targetPath)
        out += #","device_id":"# + VideoPath.quoted(deviceID)
        out += "}"
        return Data(out.utf8)
    }
}
