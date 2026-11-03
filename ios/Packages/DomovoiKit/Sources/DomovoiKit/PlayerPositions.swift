import Foundation

/// Where a podcast or an audiobook was left off, per device and per listener.
///
/// `PlayerController.kt:566-634`. Only spoken media has a place at all
/// (``PlayerItem/isSpoken``): a song does not resume.
///
/// | Route | Tier (read in the server today) |
/// |---|---|
/// | `GET /api/podcasts/positions/{id}?device_id=…&person_id=…` | none — open (`podcasts.py:523-534`) |
/// | `POST /api/podcasts/positions/{id}` | device (`podcasts.py:537-553`) |
/// | `GET /api/audiobooks/{id}/position?device_id=…&person_id=…` | device read (`audiobooks.py:599-615`) |
/// | `POST /api/audiobooks/{id}/position` | device (`audiobooks.py:617-627`) |
///
/// A **failed read starts at the top** — `0.0, 1.0` — and does not clear
/// anything (`PlayerController.kt:588`): a read that fails must not lose the
/// place by claiming zero loudly. The POST sends `position_sec` as an **Int**,
/// which is what `PositionSave` is typed as on both routes (`podcasts.py:109`,
/// `audiobooks.py:78`); a float there is a 422.
public struct PlayerPosition: Equatable, Sendable {
    public let positionSec: Double
    public let speed: Float

    public init(positionSec: Double = 0, speed: Float = 1.0) {
        self.positionSec = positionSec
        self.speed = speed
    }

    /// What the server answers when there is no row at all — that exact
    /// default, written in the route (`podcasts.py:531`).
    public static let start = PlayerPosition(positionSec: 0, speed: 1.0)
}

/// The server's wire shape for both GETs.
struct PlayerPositionRead: Decodable, Sendable {
    let positionSec: Double
    let speed: Double

    private enum CodingKeys: String, CodingKey {
        case positionSec = "position_sec"
        case speed
    }

    init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        positionSec = try values.decodeIfPresent(Double.self, forKey: .positionSec) ?? 0
        speed = try values.decodeIfPresent(Double.self, forKey: .speed) ?? 1.0
    }

    var asPosition: PlayerPosition {
        PlayerPosition(positionSec: positionSec, speed: Float(speed))
    }
}

/// The seam the **Audiobooks** item plugs into.
///
/// Android has `bookPositions: PositionSync` (`PlayerController.kt:573`): one
/// place per book across streaming and a saved copy, kept on the phone and
/// pushed to the book's own server. That belongs to Audiobooks, not here. With
/// nothing plugged in, this item uses the server slot directly — exactly as
/// Android does when `bookPositions` is null (`:580`).
public protocol PlayerPositionStore: Sendable {
    /// Whether this store, rather than the server slot, owns `item`'s place.
    func owns(_ item: PlayerItem) -> Bool

    /// Where `item` was left off, for the given server.
    func open(_ item: PlayerItem, server: PlayerServerScope?) async -> PlayerPosition

    /// Note the place. May be called every ten seconds while playing, so it
    /// must be cheap and must not block.
    func record(_ item: PlayerItem, server: PlayerServerScope?, positionSec: Double, speed: Float) async
}

/// The server slot: the two GETs and two POSTs above, in their exact shapes.
public struct PlayerServerPositions: Sendable {

    /// The route a kind's place lives at; nil for everything that has none
    /// (`PlayerController.kt:567`).
    public static func path(for item: PlayerItem) -> String? {
        switch item.kind {
        case .podcast: return "/api/podcasts/positions/\(item.itemID)"
        case .audiobook: return "/api/audiobooks/\(item.itemID)/position"
        case .library, .radio, .device: return nil
        }
    }

    /// The GET's query, in Android's order (`PlayerController.kt:591`).
    public static func readQuery(deviceID: String, personID: String?) -> String {
        var q = "?device_id=\(escaped(deviceID))"
        if let personID, !personID.isEmpty { q += "&person_id=\(escaped(personID))" }
        return q
    }

    /// A device id is generated, but a person id is a row the house named, so
    /// neither goes into a query string raw: `&` in one would split the query.
    private static func escaped(_ value: String) -> String {
        let safe = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        return value.addingPercentEncoding(withAllowedCharacters: safe) ?? value
    }

    /// The POST body (`PlayerController.kt:626-631`).
    ///
    /// `position_sec` is an **Int**, which is what makes this body different
    /// from everything else the phone sends; `person_id` is present only when
    /// a listener is identified, because the server keys the row by
    /// (device, person) and a null person is a different row from no person.
    public static func saveBody(
        deviceID: String,
        personID: String?,
        positionSec: Double,
        speed: Float
    ) -> Data {
        var body: [String: Any] = [
            "device_id": deviceID,
            "position_sec": Int(positionSec.isFinite ? max(positionSec, 0) : 0),
            "speed": Double(speed),
        ]
        if let personID, !personID.isEmpty { body["person_id"] = personID }
        return (try? JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])) ?? Data("{}".utf8)
    }
}
