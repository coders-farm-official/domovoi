import Foundation

// ===========================================================================
// Away chat's wire (1.5.0, AWAY-CHAT-WIRE v1 with its §8), as pure values:
// no sockets, no clock. The house's twin is `domovoi/away/wire.py`; where
// the wire note and that file disagree, the file is what the house runs and
// is what this follows.
// ===========================================================================
//
//     phone -> "DAC1"                               4-byte preface
//     phone -> frame(Noise IK message 1)            payload: PHONE_HELLO
//     house -> frame(Noise IK message 2)            payload: HOUSE_HELLO
//     either-> frame(Noise transport message)       payload: one message
//
// A frame is a 2-byte big-endian length (1..65535) and that many bytes. A
// message is a 6-byte header (`kind u8`, `req u32 BE`, `flags u8`) and a
// body. The phone's half of the rules is here: what it may send, and what
// it accepts back. Anything the house sends that breaks them is a
// ``AwayWireError`` and the connection is closed.

/// The stream broke the protocol. The connection is closed; nothing is said
/// to the house.
public struct AwayWireError: Error, Equatable, Sendable, CustomStringConvertible {
    public let reason: String
    public init(_ reason: String) { self.reason = reason }
    public var description: String { "away wire: \(reason)" }
}

/// A message kind (§3.1).
public enum AwayKind: UInt8, Sendable, Equatable {
    // Phone -> house.
    case req = 0x01
    case blob = 0x02
    case ping = 0x03
    case bye = 0x04
    // House -> phone.
    case resp = 0x81
    case event = 0x82
    case err = 0x83
    case pong = 0x84
    case houseBye = 0x85

    var fromHouse: Bool { rawValue & 0x80 != 0 }
}

/// One decrypted message from the house.
public struct AwayMessage: Equatable, Sendable {
    public let kind: AwayKind
    public let req: UInt32
    public let more: Bool
    public let body: Data
}

/// What the house said in its hello.
public struct AwayHouseHello: Equatable, Sendable {
    public let proto: UInt16
    public let maxPicture: UInt32
}

public enum AwayWire {

    public static let preface = Data("DAC1".utf8)
    public static let prologue = Data("domovoi-away-v1".utf8)
    public static let proto: UInt16 = 1
    /// PHONE_HELLO's `platform`: 1 Android, 2 iPhone.
    public static let platformIPhone: UInt8 = 2

    public static let maxFrame = 65_535
    public static let maxPlaintext = maxFrame - NoiseSizes.tag
    public static let headerSize = 6
    public static let flagMore: UInt8 = 0x01

    /// A REQ's JSON, all its pieces together.
    public static let maxRequest = 160 * 1024
    /// A RESP's JSON, all its pieces together.
    public static let maxResponse = 1024 * 1024
    /// One BLOB's picture bytes.
    public static let maxBlobPiece = 65_000
    public static let maxPicture = 1_048_576
    /// The home send's cap, in code points.
    public static let maxText = 32_000
    public static let maxVersion = 32
    public static let maxLimit = 50

    /// The house closes a connection after 120 s with no message; the phone
    /// pings every 45 s when it has said nothing else.
    public static let pingEverySec: Double = 45
    /// Open requests on one connection (§4.6): a fifth waits on the phone
    /// rather than drawing an `ERR busy`.
    public static let maxOpenRequests = 4

    // MARK: frames

    public static func frame(_ data: Data) throws(AwayWireError) -> Data {
        guard (1...maxFrame).contains(data.count) else { throw AwayWireError("a frame is 1..65535 bytes") }
        var out = Data([UInt8(data.count >> 8), UInt8(data.count & 0xFF)])
        out.append(data)
        return out
    }

    // MARK: the hellos

    /// The app's version as PHONE_HELLO takes it: `[0-9A-Za-z.+-]`, at most
    /// 32 characters. Anything else is dropped rather than refused, so an odd
    /// build string never stops a connection.
    public static func safeVersion(_ version: String) -> String {
        let allowed = Set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.+-")
        return String(version.filter { allowed.contains($0) }.prefix(maxVersion))
    }

    /// PHONE_HELLO (§2.2, §8 item 1: 4..36 bytes): `proto u16 BE` 1,
    /// `platform u8` 2, `ver_len u8`, then the version.
    public static func phoneHello(version: String) throws(AwayWireError) -> Data {
        let v = safeVersion(version)
        guard v == version else { throw AwayWireError("the version has characters PHONE_HELLO can't carry") }
        var out = Data([UInt8(proto >> 8), UInt8(proto & 0xFF), platformIPhone, UInt8(v.utf8.count)])
        out.append(Data(v.utf8))
        return out
    }

    /// HOUSE_HELLO: exactly 6 bytes, `proto u16 BE`, `max_picture u32 BE`.
    /// A proto this phone doesn't speak is a refusal like any other.
    public static func houseHello(_ data: Data) throws(AwayWireError) -> AwayHouseHello {
        let b = [UInt8](data)
        guard b.count == 6 else { throw AwayWireError("house hello has the wrong size") }
        let p = UInt16(b[0]) << 8 | UInt16(b[1])
        let max = UInt32(b[2]) << 24 | UInt32(b[3]) << 16 | UInt32(b[4]) << 8 | UInt32(b[5])
        guard p == proto else { throw AwayWireError("a proto this phone doesn't speak") }
        return AwayHouseHello(proto: p, maxPicture: max)
    }

    // MARK: messages

    public static func message(_ kind: AwayKind, req: UInt32, body: Data = Data(), more: Bool = false) throws(AwayWireError) -> Data {
        var out = Data([kind.rawValue])
        withUnsafeBytes(of: req.bigEndian) { out.append(contentsOf: $0) }
        out.append(more ? flagMore : 0)
        out.append(body)
        guard out.count <= maxPlaintext else { throw AwayWireError("message too large") }
        return out
    }

    /// One message from the house, its header checked: a house kind, flags
    /// with only MORE, MORE only on a RESP (an EVENT, an ERR and a BYE are
    /// never split), `req` 0 exactly for PONG and BYE, and PONG empty.
    public static func houseMessage(_ plain: Data) throws(AwayWireError) -> AwayMessage {
        let b = [UInt8](plain)
        guard b.count >= headerSize else { throw AwayWireError("short message") }
        guard let kind = AwayKind(rawValue: b[0]), kind.fromHouse else { throw AwayWireError("unknown message kind") }
        let req = UInt32(b[1]) << 24 | UInt32(b[2]) << 16 | UInt32(b[3]) << 8 | UInt32(b[4])
        let flags = b[5]
        guard flags & ~flagMore == 0 else { throw AwayWireError("unknown flags") }
        let more = flags & flagMore != 0
        let body = Data(b[headerSize...])
        if more && kind != .resp { throw AwayWireError("only a RESP is split") }
        switch kind {
        case .pong:
            guard req == 0, body.isEmpty else { throw AwayWireError("malformed pong") }
        case .houseBye:
            guard req == 0 else { throw AwayWireError("malformed bye") }
        default:
            guard req != 0 else { throw AwayWireError("req 0 is for pong and bye only") }
        }
        return AwayMessage(kind: kind, req: req, more: more, body: body)
    }

    /// A REQ body as one message, or several with MORE (at most 160 KiB in
    /// all; the house answers more with `ERR too_large`, so the phone never
    /// sends it).
    public static func requestMessages(req: UInt32, json: Data) throws(AwayWireError) -> [Data] {
        guard !json.isEmpty else { throw AwayWireError("empty request") }
        guard json.count <= maxRequest else { throw AwayWireError("request larger than 160 KiB") }
        let room = maxPlaintext - headerSize
        var out: [Data] = []
        var start = json.startIndex
        while start < json.endIndex {
            let end = json.index(start, offsetBy: room, limitedBy: json.endIndex) ?? json.endIndex
            out.append(try message(.req, req: req, body: Data([UInt8](json[start..<end])), more: end < json.endIndex))
            start = end
        }
        return out
    }

    /// A picture as BLOB messages: offsets from 0 in order, pieces of at
    /// most 65,000 bytes, adding up to exactly its size. MORE is never set
    /// (§8 item 2: the house ignores it; a picture is complete at `size`).
    public static func blobMessages(req: UInt32, picture: Data) throws(AwayWireError) -> [Data] {
        guard (1...maxPicture).contains(picture.count) else { throw AwayWireError("picture size out of range") }
        var out: [Data] = []
        var offset = 0
        let bytes = [UInt8](picture)
        while offset < bytes.count {
            let end = min(offset + maxBlobPiece, bytes.count)
            var body = Data()
            withUnsafeBytes(of: UInt32(offset).bigEndian) { body.append(contentsOf: $0) }
            body.append(contentsOf: bytes[offset..<end])
            out.append(try message(.blob, req: req, body: body))
            offset = end
        }
        return out
    }

    // MARK: JSON

    /// A phone JSON body: one object, compact UTF-8. Dictionaries can't
    /// repeat a key and the requests nest two deep, inside §3.2's rules.
    public static func json(_ object: [String: Any]) throws(AwayWireError) -> Data {
        guard JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes]) else {
            throw AwayWireError("not JSON")
        }
        return data
    }

    /// A house JSON body: one object. Unknown keys are the house's business
    /// (§3.2: a later house may add fields within the same proto).
    ///
    /// **No depth check on the way in**: §3.2 says four, but the house's own
    /// `chat.history` answer nests five deep (`messages[].images[]`), so a
    /// phone that held the house to it would refuse every chat with a picture.
    public static func houseObject(_ data: Data) throws(AwayWireError) -> [String: Any] {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw AwayWireError("not a JSON object")
        }
        return object
    }
}

/// Frames out of a byte stream, however the bytes arrive.
public struct AwayFrameReader: Sendable {
    private var buffer = Data()

    public init() {}

    public mutating func feed(_ bytes: Data) { buffer.append(bytes) }

    /// The next whole frame's payload, nil while it hasn't all arrived. A
    /// zero length is a protocol error.
    public mutating func next() throws(AwayWireError) -> Data? {
        guard buffer.count >= 2 else { return nil }
        let head = [UInt8](buffer.prefix(2))
        let size = Int(head[0]) << 8 | Int(head[1])
        guard size > 0 else { throw AwayWireError("a zero-length frame") }
        guard buffer.count >= 2 + size else { return nil }
        let bytes = [UInt8](buffer)
        let payload = Data(bytes[2..<(2 + size)])
        buffer = Data(bytes[(2 + size)...])
        return payload
    }
}

// MARK: - the request set (§4)

/// A picture declared in a `chat.send`.
public struct AwayPictureDeclaration: Equatable, Sendable {
    public let type: String
    public let size: Int
    public let sha256: String

    public init(type: String, size: Int, sha256: String) {
        self.type = type
        self.size = size
        self.sha256 = sha256
    }
}

/// The five requests (§4), and nothing else: the house refuses an unknown
/// op or key, so the request set is closed here too.
public enum AwayRequest: Equatable, Sendable {
    case list(archived: Bool, limit: Int, cursor: String?)
    case history(chatID: Int64, beforeID: Int64?, limit: Int)
    case send(chatID: Int64?, clientID: String, text: String, picture: AwayPictureDeclaration?, adminGrant: String?)
    case stream(chatID: Int64)
    case cancel(chatID: Int64)

    public var op: String {
        switch self {
        case .list: return "chat.list"
        case .history: return "chat.history"
        case .send: return "chat.send"
        case .stream: return "chat.stream"
        case .cancel: return "chat.cancel"
        }
    }

    public var isSend: Bool { if case .send = self { return true } else { return false } }

    /// Checked as the house checks it (`wire.parse_request`), so a request
    /// the house would refuse with `bad_request` never leaves the phone.
    public func validate() throws(AwayWireError) {
        func id(_ v: Int64) throws(AwayWireError) { guard v >= 1 else { throw AwayWireError("an id is 1 or more") } }
        func limit(_ v: Int) throws(AwayWireError) {
            guard (1...AwayWire.maxLimit).contains(v) else { throw AwayWireError("limit is 1..50") }
        }
        switch self {
        case .list(_, let l, let cursor):
            try limit(l)
            if let cursor {
                let ok = (1...200).contains(cursor.utf8.count) && cursor.unicodeScalars.allSatisfy {
                    CharacterSet.alphanumerics.contains($0) && $0.isASCII || $0 == "_" || $0 == "-"
                }
                guard ok else { throw AwayWireError("bad cursor") }
            }
        case .history(let c, let b, let l):
            try id(c)
            if let b { try id(b) }
            try limit(l)
        case .stream(let c), .cancel(let c):
            try id(c)
        case .send(let c, let clientID, let text, let picture, let grant):
            if let c { try id(c) }
            guard AwayRequest.isClientID(clientID) else { throw AwayWireError("client_id is a lower-case UUID") }
            guard text.unicodeScalars.count <= AwayWire.maxText else { throw AwayWireError("the message is too long") }
            guard !text.unicodeScalars.contains("\u{0}") else { throw AwayWireError("text isn't valid") }
            if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && picture == nil {
                throw AwayWireError("a message needs some words or a picture")
            }
            if let p = picture {
                guard p.type == "jpeg" || p.type == "webp" else { throw AwayWireError("picture type is jpeg or webp") }
                guard (1...AwayWire.maxPicture).contains(p.size) else { throw AwayWireError("picture size out of range") }
                guard p.sha256.count == 64, p.sha256.allSatisfy({ "0123456789abcdef".contains($0) }) else {
                    throw AwayWireError("picture sha256 is 64 lower-case hex")
                }
            }
            if let grant {
                guard (1...256).contains(grant.utf8.count), grant.utf8.allSatisfy({ (0x21...0x7e).contains($0) }) else {
                    throw AwayWireError("bad admin_grant")
                }
            }
        }
    }

    static func isClientID(_ s: String) -> Bool {
        let parts = s.split(separator: "-", omittingEmptySubsequences: false)
        guard parts.map(\.count) == [8, 4, 4, 4, 12] else { return false }
        return s.allSatisfy { "0123456789abcdef-".contains($0) }
    }

    /// The REQ body: only the op's own keys.
    public func json() throws(AwayWireError) -> Data {
        try validate()
        var out: [String: Any] = ["op": op]
        switch self {
        case .list(let archived, let limit, let cursor):
            out["archived"] = archived
            out["limit"] = limit
            out["cursor"] = cursor.map { $0 as Any } ?? NSNull()
        case .history(let chatID, let beforeID, let limit):
            out["chat_id"] = chatID
            out["before_id"] = beforeID.map { $0 as Any } ?? NSNull()
            out["limit"] = limit
        case .send(let chatID, let clientID, let text, let picture, let grant):
            out["chat_id"] = chatID.map { $0 as Any } ?? NSNull()
            out["client_id"] = clientID
            out["text"] = text
            if let picture {
                out["picture"] = ["type": picture.type, "size": picture.size, "sha256": picture.sha256]
            }
            if let grant { out["admin_grant"] = grant }
        case .stream(let chatID), .cancel(let chatID):
            out["chat_id"] = chatID
        }
        return try AwayWire.json(out)
    }

    /// A fresh `client_id`: a lower-case UUID.
    public static func newClientID() -> String { UUID().uuidString.lowercased() }
}
