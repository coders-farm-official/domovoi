import Foundation
import Observation

/// Rooms and devices asking to join (1.5.0; `docs/API_REFERENCE.md` §3.7a,
/// "Approval"): the first admin job on the phone (design §1). A device that
/// was set up shows a six-digit code; the admin types it here, picks the room
/// it is in, and the server binds it. Household words only: no ids are shown.
///
/// The port of `android/.../ui/screens/satellites/JoinApprovals.kt`
/// (bd5c4b5e), function for function; ``JoinRequestsModel`` is the card's
/// half of `JoinRequestsCard.kt`.

/// One request, as `GET /api/satellites/approvals` lists it.
public struct JoinRequest: Equatable, Sendable, Decodable, Identifiable {
    public let satelliteID: String?
    /// Repeats ``satelliteID`` for older servers.
    public let roomID: String
    public let requestedRoom: String?
    public let requestedRoomName: String?
    public let hasCode: Bool
    public let proof: Bool
    public let waitingForDevice: Bool
    public let codeRejected: Bool
    public let board: String?
    public let satType: String?
    public let lastSeenAt: String?

    public init(
        satelliteID: String? = nil, roomID: String = "", requestedRoom: String? = nil,
        requestedRoomName: String? = nil, hasCode: Bool = false, proof: Bool = false,
        waitingForDevice: Bool = false, codeRejected: Bool = false, board: String? = nil,
        satType: String? = nil, lastSeenAt: String? = nil
    ) {
        self.satelliteID = satelliteID
        self.roomID = roomID
        self.requestedRoom = requestedRoom
        self.requestedRoomName = requestedRoomName
        self.hasCode = hasCode
        self.proof = proof
        self.waitingForDevice = waitingForDevice
        self.codeRejected = codeRejected
        self.board = board
        self.satType = satType
        self.lastSeenAt = lastSeenAt
    }

    /// The id the approve and reject paths take.
    public var id: String { satelliteID ?? roomID }

    enum CodingKeys: String, CodingKey {
        case satelliteID = "satellite_id"
        case roomID = "room_id"
        case requestedRoom = "requested_room"
        case requestedRoomName = "requested_room_name"
        case hasCode = "has_code"
        case proof
        case waitingForDevice = "waiting_for_device"
        case codeRejected = "code_rejected"
        case board
        case satType = "sat_type"
        case lastSeenAt = "last_seen_at"
    }

    /// Every field optional on the way in, as the Kotlin's defaults are.
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        satelliteID = try c.decodeIfPresent(String.self, forKey: .satelliteID)
        roomID = try c.decodeIfPresent(String.self, forKey: .roomID) ?? ""
        requestedRoom = try c.decodeIfPresent(String.self, forKey: .requestedRoom)
        requestedRoomName = try c.decodeIfPresent(String.self, forKey: .requestedRoomName)
        hasCode = try c.decodeIfPresent(Bool.self, forKey: .hasCode) ?? false
        proof = try c.decodeIfPresent(Bool.self, forKey: .proof) ?? false
        waitingForDevice = try c.decodeIfPresent(Bool.self, forKey: .waitingForDevice) ?? false
        codeRejected = try c.decodeIfPresent(Bool.self, forKey: .codeRejected) ?? false
        board = try c.decodeIfPresent(String.self, forKey: .board)
        satType = try c.decodeIfPresent(String.self, forKey: .satType)
        lastSeenAt = try c.decodeIfPresent(String.self, forKey: .lastSeenAt)
    }
}

/// A room, as `GET /api/rooms` lists it (only what the picker needs).
public struct JoinRoom: Equatable, Sendable, Decodable, Hashable {
    public let roomID: String
    public let name: String

    public init(roomID: String, name: String = "") {
        self.roomID = roomID
        self.name = name
    }

    enum CodingKeys: String, CodingKey {
        case roomID = "room_id"
        case name
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        roomID = try c.decode(String.self, forKey: .roomID)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
    }
}

struct JoinRoomList: Decodable {
    let rooms: [JoinRoom]

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rooms = try c.decodeIfPresent([JoinRoom].self, forKey: .rooms) ?? []
    }

    enum CodingKeys: String, CodingKey { case rooms }
}

/// Which room the device goes in: one that exists, or a new one.
public enum JoinChoice: Equatable, Sendable, Hashable {
    case existing(roomID: String, name: String)
    case newRoom(name: String)
}

public enum JoinApprovals {

    /// "A phone", "A Raspberry Pi", "A room device": what is asking.
    public static func deviceWords(_ r: JoinRequest) -> String {
        let board = r.board?.lowercased()
        if r.board == "android_room" { return "An Android phone or tablet" }
        if r.board == "ios_room" { return "An iPhone or iPad" }
        if board?.contains("pi") == true { return "A Raspberry Pi" }
        if r.satType == "pc" || board?.contains("pc") == true { return "A computer" }
        return "A room device"
    }

    /// The request's whole line: what is asking, and for which room.
    public static func askingLine(_ r: JoinRequest) -> String {
        deviceWords(r) + (r.requestedRoomName.map { " asking to join the \($0)" } ?? " asking to join")
    }

    /// A name for a new room from the device's id: "kitchen_2" → "Kitchen 2".
    public static func newRoomName(_ r: JoinRequest) -> String {
        let spaced = r.id.replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
            .trimmingCharacters(in: .whitespaces)
        guard let first = spaced.first else { return "New room" }
        return first.uppercased() + spaced.dropFirst()
    }

    /// The picker's choices: every room, then a new one.
    public static func choices(_ r: JoinRequest, rooms: [JoinRoom]) -> [JoinChoice] {
        rooms.map { .existing(roomID: $0.roomID, name: $0.name.isEmpty ? "Unnamed room" : $0.name) }
            + [.newRoom(name: newRoomName(r))]
    }

    /// What the picker starts on (§3.7a): the room the device asked for when
    /// it exists with a name, else a new room named after the device.
    public static func preselect(_ r: JoinRequest, rooms: [JoinRoom]) -> JoinChoice {
        if let asked = r.requestedRoom.flatMap({ id in rooms.first { $0.roomID == id } }),
           let askedName = r.requestedRoomName, !askedName.trimmingCharacters(in: .whitespaces).isEmpty {
            return .existing(roomID: asked.roomID, name: asked.name.isEmpty ? askedName : asked.name)
        }
        return .newRoom(name: newRoomName(r))
    }

    /// The approve body: the code and exactly one room choice.
    public static func approveBody(code: String, choice: JoinChoice) -> Data {
        var fields = ["code": code]
        switch choice {
        case .existing(let roomID, _): fields["room_id"] = roomID
        case .newRoom(let name): fields["new_room_name"] = name.trimmingCharacters(in: .whitespaces)
        }
        return (try? JSONEncoder().encode(fields)) ?? Data("{}".utf8)
    }

    /// Six digits (the server refuses anything else).
    public static func codeOk(_ code: String) -> Bool {
        code.count == 6 && code.allSatisfy { ("0"..."9").contains($0) }
    }

    /// What a typed code may become: digits only, at most six.
    public static func cleanCode(_ typed: String) -> String {
        String(typed.filter { ("0"..."9").contains($0) }.prefix(6))
    }

    /// The line under a request: what is happening with it.
    public static func status(_ r: JoinRequest) -> String? {
        if r.waitingForDevice { return "Code sent: the device checks it on its next visit, within ten minutes." }
        if r.codeRejected { return "The last code typed wasn't this device's. Read it again from the device." }
        return nil
    }

    /// What the server's answer to approve means, in plain words.
    public static func approvedText(waitingForDevice: Bool, choice: JoinChoice) -> String {
        let room: String
        switch choice {
        case .existing(_, let name): room = name
        case .newRoom(let name): room = name.trimmingCharacters(in: .whitespaces)
        }
        return waitingForDevice
            ? "Code sent. The device joins \(room) once it checks the code, within ten minutes."
            : "Added to \(room)."
    }

    /// A refused approve, in plain words (§3.7a, §2.5).
    public static func refusalText(status: Int, detail: String?) -> String {
        switch status {
        case 422:
            if let detail, !detail.lowercased().contains("code") { return detail }
            return "That code doesn't match the one the device shows."
        case 429: return "Too many tries for this device. Wait five minutes and try again."
        case 409: return detail ?? "This request isn't waiting any more."
        case 404: return "That room is gone. Pick another."
        default: return detail ?? "That didn't work (HTTP \(status))."
        }
    }

    // The card's words (`JoinRequestsCard.kt`).
    public static let title = "Asking to join"
    public static let sub = "A room device that was set up shows a six-digit code. Type it here and pick its room."
    public static let checkWithFaceID = "check with Face ID"
    public static let checking = "checking…"
    public static let whichRoom = "Which room is it in?"
    public static let aNewRoom = "A new room"
    public static let newRoomField = "new room's name"
    public static let codeField = "the code the device shows"
    public static let approve = "approve"
    public static let turnAway = "turn away"
    public static let turnedAway = "Turned away. It keeps asking until it is switched off."

    /// How often the list is read while the phone is signed in.
    public static let pollSeconds: UInt64 = 15

    /// A failure in plain words: the server's `detail`, a reason, or the
    /// client's own message (Android's `apiErrorText`).
    public static func errorText(_ error: any Error) -> String {
        if let why = (error as? AdminSession.AdminUnavailable)?.reason { return why }
        if let none = error as? AdminSession.NotSignedIn { return none.message }
        if let failure = error as? DomovoiError {
            if let f = failure.failure, let detail = jsonDetail(of: f.body), !detail.isEmpty { return detail }
            return failure.message
        }
        return "\(error)"
    }
}

/// Settings → Rooms' "Asking to join", the card's state: the requests, the
/// rooms to pick from, and the two writes, each through the admin session.
/// `JoinRequestsCard.kt`'s logic, apart from the view.
@MainActor
@Observable
public final class JoinRequestsModel {

    /// Nil before the first read, and after one that found no session.
    public private(set) var requests: [JoinRequest]?
    public private(set) var rooms: [JoinRoom] = []
    public private(set) var error: String?

    private let admin: AdminSession
    private let client: DomovoiClient

    public init(admin: AdminSession) {
        self.admin = admin
        self.client = admin.client
    }

    public static let listPath = "/api/satellites/approvals"

    /// Read the requests: through ``AdminSession/call(_:)`` while signed in
    /// (the 15 s poll), or through ``AdminSession/act(_:)`` when only kept —
    /// the "check with Face ID" tap.
    public func load(viaAct: Bool) async {
        let client = client
        let read: @Sendable (_ headers: [String: String], _ server: String) async throws -> Data = { headers, server in
            try await client.raw(method: "GET", path: Self.listPath, headers: headers, onlyTo: server)
        }
        do {
            let data = viaAct ? try await admin.act(read) : try await admin.call(read)
            requests = try JSONDecoder().decode([JoinRequest].self, from: data)
            // The rooms are a household read: a failure keeps the last list.
            if let roomData = try? await client.raw(method: "GET", path: "/api/rooms"),
               let list = try? JSONDecoder().decode(JoinRoomList.self, from: roomData) {
                rooms = list.rooms
            }
            error = nil
        } catch is AdminSession.NotSignedIn {
            requests = nil
        } catch is DecodingError {
            error = "the server's answer made no sense"
        } catch {
            self.error = JoinApprovals.errorText(error)
        }
    }

    /// How a write went: a sentence for the shell's toast (the row may leave
    /// the list on the next read), or a note under the row.
    public enum Outcome: Equatable, Sendable {
        case done(toast: String)
        case refused(note: String)
    }

    /// Approve with `code` into `choice`, or turn away — through
    /// ``AdminSession/act(_:)``, so a tap after the hour asks for Face ID.
    public func send(_ r: JoinRequest, approve: Bool, code: String, choice: JoinChoice) async -> Outcome {
        let client = client
        let path = "\(Self.listPath)/\(encodedRoomPathSegment(r.id))/" + (approve ? "approve" : "reject")
        let body = approve ? JoinApprovals.approveBody(code: code, choice: choice) : nil
        do {
            let data = try await admin.act { headers, server in
                try await client.raw(method: "POST", path: path, body: body, headers: headers, onlyTo: server)
            }
            guard approve else { return .done(toast: JoinApprovals.turnedAway) }
            let reply = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let waiting = reply?["waiting_for_device"] as? Bool == true
            return .done(toast: JoinApprovals.approvedText(waitingForDevice: waiting, choice: choice))
        } catch {
            if let f = (error as? DomovoiError)?.failure {
                return .refused(note: JoinApprovals.refusalText(status: f.status, detail: jsonDetail(of: f.body)))
            }
            return .refused(note: JoinApprovals.errorText(error))
        }
    }
}
