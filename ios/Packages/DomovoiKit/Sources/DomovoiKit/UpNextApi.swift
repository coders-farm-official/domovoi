import Foundation

/// The phone's Up next calls (API_REFERENCE §3.5f, device tier;
/// `UpNextApi.kt`). Each names the server it is for (`server`: the address
/// the session lives on) and is never sent once the app talks to another
/// one (``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:headers:onlyTo:)``'s
/// `onlyTo`). One request each: nothing here chains a second call on an
/// answer.
///
/// On the main actor, like the player that calls it, so a test's fake can
/// hold a call open and change the world under it.
@MainActor
public protocol UpNextApi: AnyObject, Sendable {
    /// `POST /api/music/up-next/sessions`.
    func start(server: String, body: Data) async throws -> UpNextSession

    /// `POST …/{id}/next {have_through_seq}`.
    func next(server: String, id: String, haveThrough: Int) async throws -> UpNextBatch

    /// `POST …/{id}/feedback {seq, kind}`: the seqs the session took back.
    func feedback(server: String, id: String, seq: Int, kind: String) async throws -> [Int]

    /// `PATCH …/{id}` (`autoplay`, `listener_person_id`).
    func patch(server: String, id: String, body: Data) async throws -> UpNextSession?

    /// `GET …/{id}`: the session with the picks still waiting.
    func read(server: String, id: String) async throws -> UpNextSession

    /// `POST …/{id}/move {target}`.
    func move(server: String, id: String, target: Data) async throws -> UpNextSession?
}

/// ``UpNextApi`` over the app's client, naming this device in `X-Device-Id`
/// (the server compares the session's device with it: 403 otherwise).
@MainActor
public final class HttpUpNextApi: UpNextApi {

    private let client: DomovoiClient
    private let deviceID: @MainActor () -> String

    public init(client: DomovoiClient, deviceID: @escaping @MainActor () -> String) {
        self.client = client
        self.deviceID = deviceID
    }

    static func path(_ id: String? = nil, _ tail: String = "") -> String {
        var out = "/api/music/up-next/sessions"
        if let id {
            let allowed = CharacterSet.urlPathAllowed.subtracting(CharacterSet(charactersIn: "/?#"))
            out += "/" + (id.addingPercentEncoding(withAllowedCharacters: allowed) ?? id)
        }
        return out + tail
    }

    private func call(_ method: String, _ server: String, _ path: String, _ body: Data?) async throws -> Data {
        try await client.raw(
            method: method,
            path: path,
            body: body,
            headers: [deviceIDHeader: deviceID()],
            onlyTo: server
        )
    }

    public func start(server: String, body: Data) async throws -> UpNextSession {
        try Self.decode(try await call("POST", server, Self.path(), body))
    }

    public func next(server: String, id: String, haveThrough: Int) async throws -> UpNextBatch {
        let body = Self.encode(["have_through_seq": haveThrough])
        return try Self.decode(try await call("POST", server, Self.path(id, "/next"), body))
    }

    public func feedback(server: String, id: String, seq: Int, kind: String) async throws -> [Int] {
        let body = Self.encode(["seq": seq, "kind": kind])
        return Self.removed(try await call("POST", server, Self.path(id, "/feedback"), body))
    }

    public func patch(server: String, id: String, body: Data) async throws -> UpNextSession? {
        Self.sessionOrNil(try await call("PATCH", server, Self.path(id), body))
    }

    public func read(server: String, id: String) async throws -> UpNextSession {
        try Self.decode(try await call("GET", server, Self.path(id), nil))
    }

    public func move(server: String, id: String, target: Data) async throws -> UpNextSession? {
        var body = Data(#"{"target":"#.utf8)
        body.append(target)
        body.append(Data("}".utf8))
        return Self.sessionOrNil(try await call("POST", server, Self.path(id, "/move"), body))
    }

    // MARK: - reading the answers

    static func decode<T: Decodable>(_ data: Data) throws -> T {
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw DomovoiError.notDomovoi("the up next answer could not be read")
        }
    }

    /// `{removed: [seq]}`; nothing readable is none.
    static func removed(_ data: Data) -> [Int] {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let list = object["removed"] as? [Any] else { return [] }
        return list.compactMap { ($0 as? NSNumber)?.intValue }
    }

    static func sessionOrNil(_ data: Data) -> UpNextSession? {
        guard let object = try? JSONSerialization.jsonObject(with: data), object is [String: Any] else { return nil }
        return try? JSONDecoder().decode(UpNextSession.self, from: data)
    }

    static func encode(_ object: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
    }
}

/// Whether a failure is the server refusing (a 4xx: no Up next for this
/// play, say nothing) rather than the home server being out of reach (a
/// network failure or a 5xx: say so, try again on the next song).
public func upNextRefused(_ error: any Error) -> Bool {
    guard let failure = (error as? DomovoiError)?.failure else { return false }
    return (400..<500).contains(failure.status)
}

/// The status a failure carried, or nil for one that never got an answer.
public func upNextStatus(_ error: any Error) -> Int? {
    (error as? DomovoiError)?.failure?.status
}
