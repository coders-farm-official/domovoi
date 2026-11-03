import Foundation

/// Every `/api/documents` route the editors and `+ New` ask for, and every
/// body they send, escaped and built once, here.
///
/// ``FilePath`` is the same type for `/api/files`, and this one deliberately
/// does not grow into it: the two modules' routes take their arguments
/// differently. A file in `/api/files` travels as a **query value** beside a
/// library id; here `rel_path` is the **tail of the URL**
/// (`/text/{rel_path:path}`), so `/` has to survive and `?`, `#`, `+` and a
/// space must not — which is ``FilePath/escPath(_:)`` and not
/// ``FilePath/esc(_:)``. That function is reused rather than copied, because
/// one copy of an escaping rule is the whole point of either type.
///
/// ### What is not here, and why there is no control for it
///
/// * `/presence/{rel}` — the sheet editor's "somebody else has this open"
///   heartbeat. This app has no co-editing and no stale-save choice, so a
///   notice it could draw is one nothing can act on, and the heartbeat would
///   have a phone writing to the house every fifteen seconds for it.
/// * `/doc-save-as`, `/export/*`, `/convert/docx`, `/drawings/*` — features
///   of the dashboard's WebView editors, which are not ported.
/// * `/delete` and `/download-zip` — **admin** tier (`documents.py:808`,
///   `:836`), and a phone has no admin session: they answer 401 every time,
///   the same reason ``FilePath/deleteNeedsAnAdmin`` is drawn by nothing.
/// * `/upload` — device tier and perfectly callable, but it belongs to the
///   milestone that gives this app access to the phone's own files.
public enum DocumentsPath {

    // MARK: - reads and writes, one route each

    /// `GET`/`PUT /api/documents/text/{rel_path}` — the plain-text editor.
    ///
    /// The **GET has no category guard and the PUT has one**
    /// (`_assert_text_editor_may_write`, `documents.py:447`), so a load that
    /// worked proves nothing about the save. ``DocumentsKinds/editor(for:in:)``
    /// is what keeps the editor off a file the save would refuse.
    ///
    /// It is ``FilePath/documentText(rel:)`` rather than a second copy of the
    /// string: the read-only reader already asks for this route, and the two
    /// must never disagree about how a filename with a `#` in it is spelt.
    public static func text(rel: String) -> String { FilePath.documentText(rel: rel) }

    /// `GET`/`PUT /api/documents/doc/{rel_path}` — the markdown editor.
    /// `{markdown: …}` in both directions for a `.md` (`documents.py:1965`,
    /// `:1997`).
    public static func doc(rel: String) -> String {
        "/api/documents/doc/\(FilePath.escPath(rel))"
    }

    /// `GET`/`PUT /api/documents/sheet/{rel_path}` — the grid editor. The
    /// route the server keeps "for the phone app's own grid editor"
    /// (`documents.py:1314`).
    public static func sheet(rel: String) -> String {
        "/api/documents/sheet/\(FilePath.escPath(rel))"
    }

    /// `POST /api/documents/create` — `+ New`.
    public static let create = "/api/documents/create"

    // MARK: - the bodies

    /// `{"text": …}`, and this phone's id when it has one.
    ///
    /// `device_id` is **optional** on these three routes
    /// (`TextWriteRequest:636`) and typed `min_length=3`, so a short or
    /// missing id is left out rather than sent — a 422 over a field that only
    /// decides whether a per-device block applies would fail the save itself.
    /// Android sends it on `POST /create` alone (`NewDocument.kt:49`); it
    /// goes on all three here because the iOS client sends no `X-Device-Id`
    /// header, so the body is the only way an admin's block on this phone can
    /// reach a save at all.
    public nonisolated static func textBody(text: String, deviceID: String? = nil) -> Data {
        var out = #"{"text":"# + VideoPath.quoted(text)
        out += Self.deviceField(deviceID)
        return Data((out + "}").utf8)
    }

    /// `{"markdown": …}` for a `.md`.
    ///
    /// **No `allow_empty`.** The server refuses an empty save over a file
    /// that says something with a 409 (`_assert_not_blanking`, `:1861`), and
    /// `allow_empty` is the dashboard editor saying "the person really did
    /// delete everything". This app cannot tell that from a load that has not
    /// finished, so it never claims it — and shows the server's own sentence
    /// when the refusal comes.
    public nonisolated static func docBody(markdown: String, deviceID: String? = nil) -> Data {
        var out = #"{"markdown":"# + VideoPath.quoted(markdown)
        out += Self.deviceField(deviceID)
        return Data((out + "}").utf8)
    }

    /// `{"rows": [[{"v"|"f": text}]]}` — the **whole padded grid**, as
    /// Android sends it (`FileSource.kt:256-278`).
    ///
    /// Not only the changed cells: the server diffs the grid it is given
    /// against the file cell by cell (`documents.py:1275`), so a body holding
    /// just the edits would blank every cell left out of it.
    ///
    /// A cell whose text starts with `=` is a formula (`f`), everything else
    /// is a value (`v`) — including the empty string, which `_grid_model`
    /// (`:948`) reads as a blank cell that keeps its formatting.
    public nonisolated static func sheetBody(rows: [[String]], deviceID: String? = nil) -> Data {
        var out = #"{"rows":["#
        for (index, row) in rows.enumerated() {
            if index > 0 { out += "," }
            out += "["
            for (column, cell) in row.enumerated() {
                if column > 0 { out += "," }
                out += cell.hasPrefix("=")
                    ? #"{"f":"# + VideoPath.quoted(cell) + "}"
                    : #"{"v":"# + VideoPath.quoted(cell) + "}"
            }
            out += "]"
        }
        out += "]"
        out += Self.deviceField(deviceID)
        return Data((out + "}").utf8)
    }

    /// `{"name": …, "kind": …}` — the name **as typed** (the server strips
    /// any directory part and applies the kind's extension itself,
    /// `documents.py:772-785`).
    public nonisolated static func createBody(
        name: String,
        kind: DocumentsNewKind,
        deviceID: String? = nil
    ) -> Data {
        var out = #"{"name":"# + VideoPath.quoted(name.trimmingCharacters(in: .whitespacesAndNewlines))
        out += #","kind":"# + VideoPath.quoted(kind.api)
        out += Self.deviceField(deviceID)
        return Data((out + "}").utf8)
    }

    /// `,"device_id":"…"` when there is an id the server would accept, and
    /// nothing at all when there is not. `min_length=3`, `max_length=64`
    /// (`CreateRequest:620`) — Android checks the same range
    /// (`NewDocument.kt:49`).
    nonisolated static func deviceField(_ deviceID: String?) -> String {
        guard let deviceID, deviceID.count >= 3, deviceID.count <= 64 else { return "" }
        return #","device_id":"# + VideoPath.quoted(deviceID)
    }
}
