import Foundation

/// Every Audiobooks path and body in one place — ``VideoPath``'s shape, for
/// its reason: a route spelt in a view is a route nothing can test.
///
/// Each one was read from `web/backend/api/audiobooks.py` in this worktree,
/// not from memory. The two list reads and the cover are **open** (no auth
/// dependency, `main.py:444`); reindex and both position routes are
/// **device tier** or `require_device_read` (`audiobooks.py:56`, `:538`,
/// `:599`, `:617`).
///
/// ### `person_id` is deliberately absent from every one of these
///
/// Both position routes take an optional `person_id`, and Android always
/// sends one (`ApiPositionRemote.kt:26-29`) because `prefs.listenerPersonId`
/// exists there. **Nothing on iOS stores a listener person**, so every
/// request this item makes lands in the server's *unidentified listener*
/// slot — a different row from any person's
/// (`domovoi/spoken_audio.py:145-147`). Sending `person_id=` empty would be a
/// *different key* to the server rather than an absent one, which is why the
/// parameter is not here at all. DECISIONS §1 asks the owner whether the
/// phone should get a picker.
public enum AudiobookPath {

    /// Unreserved characters only, so `&`, `=`, `#`, `+`, `/`, `\` and a
    /// space are all escaped.
    ///
    /// Android's `URLEncoder.encode(…).replace("+", "%20")`
    /// (`BookModels.kt:145`) sends a space as `%20` for the reason the
    /// `replace` is there: the server reads the query string, and `+` means a
    /// space only to a form parser. This set is the same intent, strictly —
    /// it also escapes `*` and `~`, which `URLEncoder` treats differently
    /// from each other and both correctly.
    static let safe = CharacterSet(charactersIn: "-._~").union(.alphanumerics)

    static func esc(_ raw: String) -> String {
        raw.addingPercentEncoding(withAllowedCharacters: safe) ?? raw
    }

    /// The library, with the sort and the needle but **no paging** — the
    /// pager adds that (``Paging/path(_:_:)``).
    ///
    /// `AudiobooksScreen.kt:127`. A blank needle is left out rather than sent
    /// as `q=`; the server caps `q` at 200 characters (`audiobooks.py:101`)
    /// and so does this, because a 422 from a long paste is a failed list
    /// where a truncated search is a search.
    public static func list(sort: AudiobooksSort = .title, q: String = "") -> String {
        var out = "/api/audiobooks?sort=\(sort.rawValue)"
        let needle = String(q.trimmingCharacters(in: .whitespacesAndNewlines).prefix(200))
        if !needle.isEmpty { out += "&q=\(esc(needle))" }
        return out
    }

    /// One book, or a 404 `{"detail": "audiobook N not found"}`.
    public static func book(_ id: Int64) -> String {
        "/api/audiobooks/\(id)"
    }

    /// Re-walk the server's audiobooks folder. Empty body, device tier.
    public static let reindex = "/api/audiobooks/reindex"

    /// The cover, or a 404 `{"detail": "no cover art"}` for a book with
    /// none — which ``AudiobookCoverStore`` treats as settled.
    public static func cover(_ id: Int64) -> String {
        "/api/audiobooks/\(id)/cover"
    }

    /// One part's audio.
    ///
    /// `?file=` for a folder book, because the server **400s a folder book
    /// without it** (`audiobooks.py:325-326`). It serves only a name whose
    /// suffix is an audio extension, strips any path component
    /// (`Path(file).name`) and containment-checks the result inside the
    /// audiobooks directory — so a folder's `cover.jpg` is a 404 and `../`
    /// never leaves. `BookModels.kt:92`.
    public static func audio(bookID: Int64, file: String? = nil) -> String {
        var out = "/api/audiobooks/\(bookID)/audio"
        if let file { out += "?file=\(esc(file))" }
        return out
    }

    /// This part's audio, by the part ``BookRules/parts(_:)`` produced.
    public static func audio(bookID: Int64, part: BookPart) -> String {
        audio(bookID: bookID, file: part.file)
    }

    /// This phone's slot in a book. `device_id` is **required** by the route.
    public static func position(_ id: Int64, deviceID: String) -> String {
        "/api/audiobooks/\(id)/position?device_id=\(esc(deviceID))"
    }

    /// The save, which is a POST with a body.
    public static func savePosition(_ id: Int64) -> String {
        "/api/audiobooks/\(id)/position"
    }

    /// This listener's places on **other** devices and rooms; the asking
    /// device's own row is excluded by the server.
    public static func positions(_ id: Int64, deviceID: String) -> String {
        "/api/audiobooks/\(id)/positions?device_id=\(esc(deviceID))"
    }

    /// The body of a position save (`PositionSave`, `audiobooks.py:78-83`).
    ///
    /// - Parameter positionSec: **truncated to an int**, as Android does
    ///   (`ApiPositionRemote.kt:38`, `positionSec.toInt()`), because the
    ///   server types the field as an `int` and a float there is a 422. It is
    ///   clamped at zero as well: a negative position means the player
    ///   reported one, and sending it is how a slot becomes unusable.
    ///
    /// `nonisolated static` so a test off the main actor can ask for the
    /// bytes without a model.
    public nonisolated static func saveBody(
        deviceID: String,
        positionSec: Double,
        speed: Double
    ) -> Data {
        // ``VideoPath/quoted(_:)`` rather than a second copy of it: a device
        // id is the person's own label for a phone and may hold a quote or a
        // backslash, and one escaper in the module is one place for that to
        // be right.
        var out = #"{"device_id":"# + VideoPath.quoted(deviceID)
        out += #","position_sec":\#(Int64(max(0, positionSec.isFinite ? positionSec : 0)))"#
        out += #","speed":\#(speed.isFinite && speed > 0 ? speed : 1)"#
        out += "}"
        return Data(out.utf8)
    }
}

/// The server's slot for this phone as the route answers it:
/// `{position_sec, speed, updated_at}` when there is a row, and
/// **`{"position_sec": 0, "speed": 1.0}` with no `updated_at` at all** when
/// there is none (`audiobooks.py:599-615`, `spoken_audio.py:137-163`).
///
/// The **absence** of `updated_at` is the whole signal
/// ``BookPositionRules/resolve(local:server:)`` turns on, which is why this
/// decodes it as an optional and never substitutes a value for it.
public struct ServerBookPosition: Decodable, Sendable, Equatable {

    public let positionSec: Double
    public let speed: Double

    /// Nil when the server has no row for this phone.
    public let updatedAt: String?

    public init(positionSec: Double, speed: Double, updatedAt: String?) {
        self.positionSec = positionSec
        self.speed = speed
        self.updatedAt = updatedAt
    }

    private enum CodingKeys: String, CodingKey {
        case positionSec = "position_sec"
        case speed
        case updatedAt = "updated_at"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        positionSec = try values.decodeIfPresent(Double.self, forKey: .positionSec) ?? 0
        speed = try values.decodeIfPresent(Double.self, forKey: .speed) ?? 1
        // `takeIf { it.isNotBlank() }` (`ApiPositionRemote.kt:54`): a server
        // that answered an empty string has not got a row either.
        let stamp = try values.decodeIfPresent(String.self, forKey: .updatedAt)
        updatedAt = (stamp?.trimmingCharacters(in: .whitespacesAndNewlines)).flatMap { $0.isEmpty ? nil : stamp }
    }

    /// Whether the server holds a place for this phone at all.
    public var hasRow: Bool { updatedAt != nil }
}

/// What `POST /api/audiobooks/reindex` answers: `index_audiobooks_dir`'s
/// counts (`workers/audiobook_indexer.py:452-527`).
///
/// Android reads only `scanned` and says `Indexed N book(s)`
/// (`AudiobooksScreen.kt:170-172`); the other three are decoded because the
/// answer has them and a reader of this type should not have to guess what
/// came back.
public struct AudiobookReindexResult: Decodable, Sendable, Equatable {

    public let scanned: Int
    public let inserted: Int
    public let errors: Int
    public let pruned: Int

    public init(scanned: Int = 0, inserted: Int = 0, errors: Int = 0, pruned: Int = 0) {
        self.scanned = scanned
        self.inserted = inserted
        self.errors = errors
        self.pruned = pruned
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        scanned = try values.decodeIfPresent(Int.self, forKey: .scanned) ?? 0
        inserted = try values.decodeIfPresent(Int.self, forKey: .inserted) ?? 0
        errors = try values.decodeIfPresent(Int.self, forKey: .errors) ?? 0
        pruned = try values.decodeIfPresent(Int.self, forKey: .pruned) ?? 0
    }

    private enum CodingKeys: String, CodingKey {
        case scanned, inserted, errors, pruned
    }
}
