import Foundation

/// Why a room code was refused: the contract's stable reason codes
/// (`docs/uml/spare-device-enrol-fixture.json` "reasons", in its order).
public enum RoomEnrolmentReason: String, CaseIterable, Equatable, Sendable {
    case notEnrolment = "not_enrolment"
    case unsupportedVersion = "unsupported_version"
    case malformed
    case missingCore = "missing_core"
    case missingFp = "missing_fp"
    case missingRoom = "missing_room"
    case badCore = "bad_core"
    case notHomeNetwork = "not_home_network"
    case badFp = "bad_fp"
    case badRoom = "bad_room"
}

/// A refusal: the code, and words for the screen.
public struct RoomEnrolmentRefusal: Error, Equatable, Sendable {
    public let reason: RoomEnrolmentReason
    public let words: String
}

/// What a scanned or pasted text is.
public enum RoomEnrolmentParse: Equatable, Sendable {
    case ok(RoomEnrolment)
    case invalid(RoomEnrolmentRefusal)

    public var enrolment: RoomEnrolment? {
        if case .ok(let e) = self { return e }
        return nil
    }

    public var refusal: RoomEnrolmentRefusal? {
        if case .invalid(let r) = self { return r }
        return nil
    }
}

/// The room code a phone scans or pastes to become a room
/// (`roommode/RoomEnrolment.kt`, a line-for-line port of the reference
/// parser `satellite/enrolment.py`), held to the shared contract
/// `docs/uml/spare-device-enrol-fixture.json` (`RoomEnrolmentParserTests`).
///
///     domovoi-room:1?core=<core base URL>&fp=<SHA256:… fingerprint>&room=<room name>
///
/// percent-encoded UTF-8, with no secret in it. The dashboard's QR code
/// carries it inside a join link, `<core>/join#<payload>`; ``parse(_:allowingLoopback:)``
/// takes either form.
///
/// **Worked on Unicode scalars, never on `Character`s.** A Swift `Character`
/// is a grapheme cluster: `"\r\n"` is one, and so is `"&"` followed by a
/// combining mark — so splitting or trimming by `Character` would read a
/// different payload than Kotlin's `String` (UTF-16) and Python's `str` (code
/// points) do. Every structural character here is ASCII, so code points give
/// the contract's answer.
public enum RoomEnrolmentParser {

    public static let scheme = "domovoi-room"
    public static let version = "1"
    public static let joinPath = "/join"
    public static let maxPayloadLength = 1024
    public static let maxCoreLength = 256
    /// In code points, after decoding.
    public static let maxRoomNameLength = 64
    public static let maxRoomIDLength = 32
    /// The core's LAN names (`domovoi/transport_guard.py`, less .localhost).
    public static let homeSuffixes = [".local", ".home.arpa", ".lan", ".internal"]

    typealias Scalars = [Unicode.Scalar]

    /// Parse a scanned or pasted text.
    ///
    /// `allowingLoopback` is for the UI-test launch only (`-domovoi-room`,
    /// `#if DEBUG` in the app): the stub house is on 127.0.0.1, and a real
    /// room code to a loopback address is `not_home_network` like any other
    /// address off the home network (ROOMMODE-MAP §13.4). Nothing else passes
    /// it.
    public static func parse(_ text: String?, allowingLoopback: Bool = false) -> RoomEnrolmentParse {
        do {
            return .ok(try parseOrThrow(Array((text ?? "").unicodeScalars), loopback: allowingLoopback))
        } catch let refusal as RoomEnrolmentRefusal {
            return .invalid(refusal)
        } catch {
            return .invalid(RoomEnrolmentRefusal(reason: .malformed, words: "\(error)"))
        }
    }

    /// The satellite socket's base: the core URL with ws/wss.
    public static func wsURL(for e: RoomEnrolment) -> String {
        if e.coreURL.hasPrefix("https://") { return "wss://" + e.coreURL.dropFirst("https://".count) }
        return "ws://" + e.coreURL.dropFirst("http://".count)
    }

    private static func refuse(_ reason: RoomEnrolmentReason, _ words: String) -> RoomEnrolmentRefusal {
        RoomEnrolmentRefusal(reason: reason, words: words)
    }

    private static func string(_ s: ArraySlice<Unicode.Scalar>) -> String {
        var view = String.UnicodeScalarView()
        view.append(contentsOf: s)
        return String(view)
    }

    private static func string(_ s: Scalars) -> String { string(s[...]) }

    private static func parseOrThrow(_ payload: Scalars, loopback: Bool) throws -> RoomEnrolment {
        // A paste brings a newline or a space along; only those are trimmed.
        let trimmable: Set<Unicode.Scalar> = [" ", "\t", "\r", "\n"]
        var lo = payload.startIndex
        var hi = payload.endIndex
        while lo < hi, trimmable.contains(payload[lo]) { lo += 1 }
        while hi > lo, trimmable.contains(payload[hi - 1]) { hi -= 1 }
        let text = Array(payload[lo..<hi])
        let head = asciiLower(Array(text.prefix(8)))
        if hasPrefix(head, "http://") || hasPrefix(head, "https://") {
            return try parseJoinLink(text, loopback: loopback)
        }
        return try parsePayload(text, loopback: loopback)
    }

    /// `<core>/join#<payload>` (`RoomEnrolment.kt:122-135`).
    private static func parseJoinLink(_ text: Scalars, loopback: Bool) throws -> RoomEnrolment {
        let hash = text.firstIndex(of: "#")
        let base = hash.map { Array(text[..<$0]) } ?? text
        if hash == nil || !hasSuffix(asciiLower(base), joinPath) {
            throw refuse(.notEnrolment, "this link is not a Domovoi room code (<server>/join#\(scheme):…)")
        }
        if string(base).utf16.count > maxCoreLength + joinPath.count {
            throw refuse(.malformed, "the link's server address is too long")
        }
        let e = try parsePayload(Array(text[(hash! + 1)...]), loopback: loopback)
        if string(asciiLower(base)) != string(asciiLower(Array((e.coreURL + joinPath).unicodeScalars))) {
            throw refuse(.malformed, "the link's server is not the one its room code names")
        }
        return e
    }

    private static func parsePayload(_ text: Scalars, loopback: Bool) throws -> RoomEnrolment {
        guard let colon = text.firstIndex(of: ":"), string(asciiLower(Array(text[..<colon]))) == scheme else {
            throw refuse(.notEnrolment, "this is not a Domovoi room code (it starts with \(scheme):)")
        }
        if text.count > maxPayloadLength {
            throw refuse(.malformed, "the room code is longer than \(maxPayloadLength) characters")
        }
        let rest = Array(text[(colon + 1)...])
        let q = rest.firstIndex(of: "?")
        let versionScalars = q.map { Array(rest[..<$0]) } ?? rest
        if versionScalars.isEmpty || !versionScalars.allSatisfy(isDigit) {
            throw refuse(.malformed, "the room code has no version (domovoi-room:1?…)")
        }
        let v = string(versionScalars)
        if v != version {
            throw refuse(.unsupportedVersion, "this room code is version \(v); this app understands \(version) — update the app")
        }
        guard let q else { throw refuse(.malformed, "the room code has no fields") }
        let query = Array(rest[(q + 1)...])
        if query.contains("#") { throw refuse(.malformed, "the room code must not carry a fragment (#)") }
        var fields: [String: String] = [:]
        for pair in split(query, on: "&") {
            let eq = pair.firstIndex(of: "=")
            let keyScalars = eq.map { Array(pair[..<$0]) } ?? pair
            let key = string(keyScalars)
            if eq == nil || !isKey(keyScalars) {
                throw refuse(.malformed, "'\(string(Array(pair.prefix(40))))' is not a key=value field")
            }
            if fields[key] != nil { throw refuse(.malformed, "the field \(key) is given twice") }
            fields[key] = try decode(Array(pair[(eq! + 1)...]))
        }
        let missing: [(String, RoomEnrolmentReason)] = [("core", .missingCore), ("fp", .missingFp), ("room", .missingRoom)]
        for (key, why) in missing where (fields[key] ?? "").isEmpty {
            throw refuse(why, "the room code has no \(key)")
        }
        let core = try normalizeCore(fields["core"] ?? "", loopback: loopback)
        let fp = fields["fp"] ?? ""
        if !isFingerprint(fp) { throw refuse(.badFp, "the fingerprint must be SHA256: and 43 base64 characters") }
        let room = try checkRoomName(fields["room"] ?? "")
        return RoomEnrolment(coreURL: core, fingerprint: fp, roomID: try roomIDOrThrow(room), roomLabel: room)
    }

    // MARK: - the room id

    /// The room id a room named `name` connects as (`room_id_for`): NFKD,
    /// combining marks (Mn) and apostrophes (' and ’) dropped, A-Z
    /// lower-cased, a-z and 0-9 kept, every other run of characters one `_`
    /// with none at either end, `room_` before a leading digit; nil when
    /// nothing is left or it is over 32 characters.
    public static func roomID(for name: String) -> String? {
        try? roomIDOrThrow(name)
    }

    private static func roomIDOrThrow(_ name: String) throws -> String {
        var out = ""
        var gap = false
        for scalar in name.decomposedStringWithCompatibilityMapping.unicodeScalars {
            if scalar.properties.generalCategory == .nonspacingMark { continue }
            if scalar == "'" || scalar == "\u{2019}" { continue }
            var v = scalar.value
            if v >= 0x41 && v <= 0x5A { v += 32 }
            if (v >= 0x61 && v <= 0x7A) || (v >= 0x30 && v <= 0x39) {
                if gap && !out.isEmpty { out.append("_") }
                gap = false
                out.unicodeScalars.append(Unicode.Scalar(v)!)
            } else {
                gap = true
            }
        }
        if let first = out.unicodeScalars.first, isDigit(first) { out = "room_" + out }
        if out.isEmpty {
            throw refuse(.badRoom, "the room name has no letter or digit to make a room id from — add one (A-Z or 0-9)")
        }
        if out.count > maxRoomIDLength {
            throw refuse(.badRoom, "the room id '\(out)' is longer than \(maxRoomIDLength) characters — pick a shorter name")
        }
        return out
    }

    /// `check_room_name`: not empty, at most 64 code points, no control or
    /// format characters (Cc, Cf), and a room id can be made from it. Not
    /// trimmed.
    private static func checkRoomName(_ name: String) throws -> String {
        if name.isEmpty { throw refuse(.badRoom, "the room name is empty") }
        if name.unicodeScalars.count > maxRoomNameLength {
            throw refuse(.badRoom, "the room name is longer than \(maxRoomNameLength) characters")
        }
        for scalar in name.unicodeScalars {
            let category = scalar.properties.generalCategory
            if category == .control || category == .format {
                throw refuse(.badRoom, "the room name has control characters")
            }
        }
        _ = try roomIDOrThrow(name)
        return name
    }

    /// A room name a person typed on this phone (trimmed, as the dashboard's
    /// builder trims), or the room-name rules' refusal.
    public static func typedRoomName(_ typed: String) -> RoomEnrolmentRefusal? {
        do {
            _ = try checkRoomName(typed.trimmingCharacters(in: .whitespacesAndNewlines))
            return nil
        } catch let r as RoomEnrolmentRefusal {
            return r
        } catch {
            return refuse(.badRoom, "\(error)")
        }
    }

    // MARK: - the core URL

    /// `normalize_core` (`RoomEnrolment.kt:247-307`).
    private static func normalizeCore(_ text: String, loopback: Bool) throws -> String {
        if text.isEmpty || text.utf16.count > maxCoreLength {
            throw refuse(.badCore, "the server address is empty or too long")
        }
        let scalars = Array(text.unicodeScalars)
        if scalars.contains(where: { $0.value < 0x21 || $0.value > 0x7E }) {
            throw refuse(.badCore, "the server address has spaces or non-ASCII characters")
        }
        let lower = asciiLower(scalars)
        let scheme: String
        if hasPrefix(lower, "https://") {
            scheme = "https"
        } else if hasPrefix(lower, "http://") {
            scheme = "http"
        } else {
            throw refuse(.badCore, "the server address must start with http:// or https://")
        }
        let after = Array(scalars[(scheme.count + 3)...])
        let end = after.firstIndex { $0 == "/" || $0 == "?" || $0 == "#" }
        let netloc = end.map { Array(after[..<$0]) } ?? after
        let path = end.map { string(Array(after[$0...])) } ?? ""
        if netloc.contains("@") { throw refuse(.badCore, "the server address must not carry a user name") }
        if (path != "" && path != "/") || scalars.contains("?") || scalars.contains("#") {
            throw refuse(.badCore, "the server address must be just scheme://host:port")
        }
        let host: String
        let shown: String
        let rest: Scalars
        if netloc.first == "[" {
            guard let close = netloc.firstIndex(of: "]") else {
                throw refuse(.badCore, "the server address has an unclosed IPv6 bracket")
            }
            host = string(asciiLower(Array(netloc[1..<close])))
            rest = Array(netloc[(close + 1)...])
            if host.contains("%") { throw refuse(.badCore, "an IPv6 zone id (%…) is not supported") }
            if ipv6Bytes(host) == nil { throw refuse(.badCore, "'\(host)' is not an IPv6 address") }
            shown = "[\(host)]"
        } else {
            let c = netloc.firstIndex(of: ":")
            host = string(asciiLower(c.map { Array(netloc[..<$0]) } ?? netloc))
            rest = c.map { Array(netloc[$0...]) } ?? []
            if host.isEmpty || !isHostName(host) {
                throw refuse(.badCore, "'\(host)' is not a host name or IPv4 address")
            }
            shown = host
        }
        var port = ""
        if !rest.isEmpty {
            let digits = Array(rest.dropFirst())
            if rest.first != ":" || digits.isEmpty || !digits.allSatisfy(isDigit) {
                throw refuse(.badCore, "the server address has a bad port")
            }
            var trimmed = string(digits)
            while trimmed.hasPrefix("0") { trimmed.removeFirst() }
            if trimmed.isEmpty { trimmed = "0" }
            let number = trimmed.count > 5 ? 0 : (Int(trimmed) ?? 0)
            if !(1...65535).contains(number) { throw refuse(.badCore, "the server address's port is out of range") }
            port = ":\(number)"
        }
        if !isHomeNetworkHost(host) && !(loopback && isLoopbackHost(host)) {
            throw refuse(
                .notHomeNetwork,
                "\(host) is not on the home network (a private address, or a name ending in .local, .home.arpa, .lan "
                    + "or .internal): a room must not stream its microphone out of the house"
            )
        }
        return "\(scheme)://\(shown)\(port)"
    }

    /// `is_home_network_host`: an RFC 1918 IPv4 address, an IPv6
    /// unique-local (fc00::/7) or link-local (fe80::/10) address, or a name
    /// ending in .local, .home.arpa, .lan or .internal with something before
    /// it. Not loopback, not 100.64/10, not 169.254/16, not a public address
    /// or name, even over https: a room code decides where a room's
    /// microphone goes.
    public static func isHomeNetworkHost(_ host: String) -> Bool {
        if let b = ipv4Bytes(host) {
            let a = b[0]
            let s = b[1]
            return a == 10 || (a == 172 && (16...31).contains(s)) || (a == 192 && s == 168)
        }
        if host.contains(":") {
            guard let b = ipv6Bytes(host) else { return false }
            return (b[0] & 0xfe) == 0xfc || (b[0] == 0xfe && (b[1] & 0xc0) == 0x80)
        }
        return homeSuffixes.contains { host.hasSuffix($0) && host.count > $0.count }
    }

    /// The UI-test launch's one extra: the stub house's loopback address.
    private static func isLoopbackHost(_ host: String) -> Bool {
        if let b = ipv4Bytes(host) { return b[0] == 127 }
        return host == "::1" || host == "localhost"
    }

    /// Python's `ipaddress.IPv4Address`: four decimal octets, no leading zeros.
    static func ipv4Bytes(_ text: String) -> [Int]? {
        let parts = text.components(separatedBy: ".")
        guard parts.count == 4 else { return nil }
        var out: [Int] = []
        for p in parts {
            let s = Array(p.unicodeScalars)
            if s.isEmpty || s.count > 3 || !s.allSatisfy(isDigit) { return nil }
            if s.count > 1 && s[0] == "0" { return nil }
            guard let v = Int(p), v <= 255 else { return nil }
            out.append(v)
        }
        return out
    }

    /// Python's `ipaddress.IPv6Address` (no zone id): 16 bytes, or nil.
    static func ipv6Bytes(_ text: String) -> [Int]? {
        if text.isEmpty || text.unicodeScalars.filter({ $0 == ":" }).count < 2 { return nil }
        let halves = text.components(separatedBy: "::")
        if halves.count > 2 { return nil }
        func groups(_ part: String, allowV4Tail: Bool) -> [Int]? {
            if part.isEmpty { return [] }
            let pieces = part.components(separatedBy: ":")
            var out: [Int] = []
            for (i, p) in pieces.enumerated() {
                if i == pieces.count - 1 && allowV4Tail && p.contains(".") {
                    guard let v4 = ipv4Bytes(p) else { return nil }
                    out.append((v4[0] << 8) | v4[1])
                    out.append((v4[2] << 8) | v4[3])
                    continue
                }
                let s = Array(p.unicodeScalars)
                if s.isEmpty || s.count > 4 || !s.allSatisfy(isHex) { return nil }
                guard let v = Int(p, radix: 16) else { return nil }
                out.append(v)
            }
            return out
        }
        let all: [Int]
        if halves.count == 2 {
            guard let head = groups(halves[0], allowV4Tail: false),
                  let tail = groups(halves[1], allowV4Tail: true)
            else { return nil }
            if head.count + tail.count > 7 { return nil }
            all = head + Array(repeating: 0, count: 8 - head.count - tail.count) + tail
        } else {
            guard let g = groups(text, allowV4Tail: true), g.count == 8 else { return nil }
            all = g
        }
        return (0..<16).map { i in i % 2 == 0 ? all[i / 2] >> 8 : all[i / 2] & 0xff }
    }

    // MARK: - percent-encoding

    /// RFC 3986 query characters a value may carry raw. `+` is a plus sign.
    private static let rawOK: Set<Unicode.Scalar> = Set(
        ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789" + "-._~" + "!$'()*+,;" + ":@/?").unicodeScalars
    )

    /// One value, strictly: raw only what a URI query may carry, every `%`
    /// two hex digits, the bytes UTF-8. `+` is a plus sign, never a space.
    static func decode(_ value: Scalars) throws -> String {
        var raw: [UInt8] = []
        var i = 0
        while i < value.count {
            let ch = value[i]
            if ch == "%" {
                if i + 2 >= value.count { throw refuse(.malformed, "a %-escape is not two hex digits") }
                let a = value[i + 1]
                let b = value[i + 2]
                guard isHex(a), isHex(b), let byte = UInt8(string([a, b]), radix: 16) else {
                    throw refuse(.malformed, "a %-escape is not two hex digits")
                }
                raw.append(byte)
                i += 3
                continue
            }
            if !rawOK.contains(ch) { throw refuse(.malformed, "the character '\(ch)' must be percent-encoded") }
            raw.append(UInt8(ch.value))
            i += 1
        }
        // Strict UTF-8: Swift's decoder puts U+FFFD where the bytes are not
        // UTF-8 (overlong forms, surrogates, a cut sequence), so a text that
        // does not give the same bytes back was not UTF-8.
        let decoded = String(decoding: raw, as: UTF8.self)
        if Array(decoded.utf8) != raw { throw refuse(.malformed, "a value is not UTF-8") }
        return decoded
    }

    // MARK: - small ASCII rules

    private static func isDigit(_ s: Unicode.Scalar) -> Bool { s.value >= 0x30 && s.value <= 0x39 }

    private static func isLowerOrDigit(_ s: Unicode.Scalar) -> Bool {
        (s.value >= 0x61 && s.value <= 0x7A) || isDigit(s)
    }

    private static func isHex(_ s: Unicode.Scalar) -> Bool {
        isDigit(s) || (s.value >= 0x61 && s.value <= 0x66) || (s.value >= 0x41 && s.value <= 0x46)
    }

    /// `^[a-z][a-z0-9_]{0,31}$`.
    private static func isKey(_ s: Scalars) -> Bool {
        guard let first = s.first, first.value >= 0x61 && first.value <= 0x7A, s.count <= 32 else { return false }
        return s.dropFirst().allSatisfy { isLowerOrDigit($0) || $0 == "_" }
    }

    /// `^SHA256:[A-Za-z0-9+/]{43}$`.
    private static func isFingerprint(_ fp: String) -> Bool {
        let s = Array(fp.unicodeScalars)
        let prefix = Array("SHA256:".unicodeScalars)
        guard s.count == prefix.count + 43, Array(s.prefix(prefix.count)) == prefix else { return false }
        return s.dropFirst(prefix.count).allSatisfy { c in
            (c.value >= 0x41 && c.value <= 0x5A) || (c.value >= 0x61 && c.value <= 0x7A) || isDigit(c) || c == "+" || c == "/"
        }
    }

    /// `[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?`, the whole host.
    private static func isHostName(_ host: String) -> Bool {
        let s = Array(host.unicodeScalars)
        guard let first = s.first, let last = s.last, isLowerOrDigit(first), s.count <= 253 else { return false }
        if s.count == 1 { return true }
        guard isLowerOrDigit(last) else { return false }
        return s.dropFirst().dropLast().allSatisfy { isLowerOrDigit($0) || $0 == "." || $0 == "-" }
    }

    /// Lower-cases A-Z only (Unicode case mapping differs between runtimes).
    private static func asciiLower(_ s: Scalars) -> Scalars {
        s.map { $0.value >= 0x41 && $0.value <= 0x5A ? Unicode.Scalar($0.value + 32)! : $0 }
    }

    private static func hasPrefix(_ s: Scalars, _ prefix: String) -> Bool {
        let p = Array(prefix.unicodeScalars)
        return s.count >= p.count && Array(s.prefix(p.count)) == p
    }

    private static func hasSuffix(_ s: Scalars, _ suffix: String) -> Bool {
        let p = Array(suffix.unicodeScalars)
        return s.count >= p.count && Array(s.suffix(p.count)) == p
    }

    private static func split(_ s: Scalars, on separator: Unicode.Scalar) -> [Scalars] {
        var out: [Scalars] = [[]]
        for c in s {
            if c == separator { out.append([]) } else { out[out.count - 1].append(c) }
        }
        return out
    }
}
