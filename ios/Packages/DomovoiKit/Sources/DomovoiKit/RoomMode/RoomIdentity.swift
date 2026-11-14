import CryptoKit
import Foundation

/// "Is this the server this room belongs to?" — the room phone's port of the
/// Pi satellite's checks (satellite/server_identity.py), byte for byte where
/// bytes are signed, as `roommode/RoomIdentity.kt` ports them:
///
/// - ``verifyServer(httpBase:fingerprint:fetch:)`` /
///   ``verifyHealthDocument(_:challenge:expected:addr:)``:
///   `GET <core>/v1/health?challenge=<nonce>&addr=<host:port>` must come back
///   signed by the PINNED key over our nonce AND the address we dialed. The
///   core refuses to sign for an address that is not its own, so a LAN host
///   that relays the question cannot pass the answer off as its own. Run
///   before every connect of the room's socket. Not the app's
///   `IdentityGate`: that proves the WEB backend, and the room talks to the
///   core and holds no household token.
/// - ``verifyManifestEnvelope(_:channel:expected:)`` /
///   ``checkFreshness(_:last:)``: a file list (wake models, sounds) is taken
///   only inside an envelope our server signed (`signature_v2`, over the
///   list, its issue time and serial), and never one older than the last
///   this phone accepted on that channel.
///
/// A room phone is always pinned (the enrolment carries the fingerprint), so
/// the unpinned trust-on-first-use branches of the Python have no twin here.
public enum RoomIdentity {
    public static let algorithm = "ed25519"
    public static let fingerprintPrefix = "SHA256:"
    public static let healthContext = "domovoi-health-v1"
    public static let manifestContextV2 = "domovoi-manifest-v2"

    public static let soundsChannel = "satellite-sounds"
    public static let wakeModelsChannel = "satellite-wake-models"
    /// domovoi/wake_base_models.py WAKE_BASE_CHANNEL.
    public static let wakeBaseChannel = "satellite-wake-base"

    private static let challengeBytes = 16

    /// A server that could not prove it is ours; `message` says why.
    public struct Failure: Error, Equatable, CustomStringConvertible {
        public let message: String
        public init(_ message: String) { self.message = message }
        public var description: String { message }
    }

    public static func newChallenge() -> String {
        var rng = SystemRandomNumberGenerator()
        return (0..<challengeBytes).map { _ in String(format: "%02x", UInt8.random(in: 0...255, using: &rng)) }.joined()
    }

    public static func fingerprintOf(_ publicKey: Data) -> String {
        let digest = Data(SHA256.hash(data: publicKey))
        var b64 = digest.base64EncodedString()
        while b64.hasSuffix("=") { b64.removeLast() }
        return fingerprintPrefix + b64
    }

    /// `domovoi-health-v1\n<challenge>[\n<addr>]` (server_identity.health_message).
    public static func healthMessage(challenge: String, addr: String?) -> Data {
        var text = healthContext + "\n" + challenge
        if let addr { text += "\n" + addr }
        return Data(text.utf8)
    }

    /// The `host:port` a URL dials, spelled the way the core is asked to sign
    /// it: host lower-cased, an IPv6 literal in brackets (not re-spelled), the
    /// port always present (`dialed_address`). `RoomIdentity.kt:73-95`.
    public static func dialedAddress(_ httpBase: String) throws -> String {
        var text = httpBase.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.contains("://") { text = "http://" + text }
        let split = text.range(of: "://")!
        let scheme = text[..<split.lowerBound].lowercased()
        let after = String(text[split.upperBound...])
        let end = after.firstIndex { $0 == "/" || $0 == "?" || $0 == "#" } ?? after.endIndex
        var netloc = String(after[..<end])
        if let at = netloc.lastIndex(of: "@") { netloc = String(netloc[netloc.index(after: at)...]) }
        let host: String
        let portText: String
        if netloc.hasPrefix("[") {
            guard let close = netloc.firstIndex(of: "]") else { throw Failure("\(httpBase) is not an address") }
            host = "[" + netloc[netloc.index(after: netloc.startIndex)..<close].lowercased() + "]"
            var rest = String(netloc[netloc.index(after: close)...])
            if rest.hasPrefix(":") { rest.removeFirst() }
            portText = rest
        } else if let colon = netloc.firstIndex(of: ":") {
            host = netloc[..<colon].lowercased()
            portText = String(netloc[netloc.index(after: colon)...])
        } else {
            host = netloc.lowercased()
            portText = ""
        }
        if host.isEmpty || host == "[]" { throw Failure("\(httpBase) is not an address") }
        let port: Int
        if let p = Int(portText), (0...65_535).contains(p), portText.allSatisfy({ $0.isASCII && $0.isNumber }) {
            port = p
        } else {
            port = (scheme == "https" || scheme == "wss") ? 443 : 80
        }
        return "\(host):\(port)"
    }

    /// The health URL with both query values (`fetch_health`).
    public static func healthURL(httpBase: String, challenge: String, addr: String) -> String {
        var base = httpBase
        while base.hasSuffix("/") { base.removeLast() }
        return base + "/v1/health?challenge=" + challenge + "&addr=" + formEncode(addr)
    }

    /// Java's `URLEncoder.encode(s, "UTF-8")` with `+` spelled `%20`: letters,
    /// digits and `.-*_` as they are, every other byte `%XX`.
    public static func formEncode(_ s: String) -> String {
        var out = ""
        for byte in s.utf8 {
            switch byte {
            case UInt8(ascii: "a")...UInt8(ascii: "z"), UInt8(ascii: "A")...UInt8(ascii: "Z"),
                 UInt8(ascii: "0")...UInt8(ascii: "9"),
                 UInt8(ascii: "."), UInt8(ascii: "-"), UInt8(ascii: "*"), UInt8(ascii: "_"):
                out.unicodeScalars.append(Unicode.Scalar(byte))
            default:
                out += String(format: "%%%02X", byte)
            }
        }
        return out
    }

    /// Prove the core at `httpBase` is the one pinned as `fingerprint`: ask
    /// `fetch` for the health answer (a GET returning the body, throwing on
    /// any transport failure or non-200) and judge it. Returns the proved
    /// fingerprint; throws ``Failure`` otherwise.
    public static func verifyServer(httpBase: String, fingerprint: String, fetch: (String) throws -> String) throws -> String {
        let challenge = newChallenge()
        let addr = try dialedAddress(httpBase)
        let body: String
        do {
            body = try fetch(healthURL(httpBase: httpBase, challenge: challenge, addr: addr))
        } catch {
            var base = httpBase
            while base.hasSuffix("/") { base.removeLast() }
            throw Failure("could not reach \(base): \(RoomIdentity.describe(error))")
        }
        return try verifyHealthDocument(RoomJSON.parse(body), challenge: challenge, expected: fingerprint, addr: addr)
    }

    /// `verify_health_document`, pinned and address-bound.
    public static func verifyHealthDocument(_ doc: RoomJSON?, challenge: String, expected: String, addr: String) throws -> String {
        guard case .object(let o)? = doc else { throw Failure("the server's answer was not a document") }
        guard case .object(let identity)? = o["identity"] else {
            throw Failure("the server offered no identity to check (an older Domovoi server?)")
        }
        guard str(identity, "algorithm") == algorithm else {
            throw Failure("unsupported identity algorithm \(str(identity, "algorithm") ?? "null")")
        }
        guard let publicKey = unb64(str(identity, "public_key")), let signature = unb64(str(identity, "signature")) else {
            if str(identity, "addr_refused") == addr {
                throw Failure(
                    "the server would not sign for \(addr): that address is not the server's own " +
                    "(something is relaying its answers, or the server needs it in TRUSTED_HOSTS)"
                )
            }
            throw Failure("the server's identity is malformed")
        }
        guard str(identity, "challenge") == challenge else { throw Failure("the server answered a different challenge") }
        guard let signedAddr = str(identity, "addr") else {
            throw Failure("the proof is not bound to the address dialed (\(addr)): an older Domovoi server, or something relaying one")
        }
        guard signedAddr == addr else { throw Failure("the signed address \(signedAddr) is not the one dialed (\(addr))") }
        guard Ed25519Verify.verify(publicKey: publicKey, message: healthMessage(challenge: challenge, addr: addr), signature: signature) else {
            throw Failure("the server's signature did not verify")
        }
        let actual = fingerprintOf(publicKey)
        if let claimed = str(identity, "fingerprint"), claimed.hasPrefix(fingerprintPrefix), claimed != actual {
            throw Failure("the server's fingerprint does not match its key")
        }
        guard actual == expected else {
            throw Failure("this is a different server: \(actual) is not the \(expected) this room was set up for")
        }
        return actual
    }

    // MARK: - signed file lists

    /// The `{path: sha256}` list inside a signed envelope (`manifest.sig`), or
    /// throw: our server's key (`expected`), the channel it was signed for,
    /// an integer `issued_at` and `serial`, and `signature_v2` over all of it
    /// (`verify_manifest_envelope`).
    public static func verifyManifestEnvelope(_ doc: RoomJSON?, channel: String, expected: String) throws -> RoomSignedList {
        guard case .object(let o)? = doc else { throw Failure("the signed list was not a document") }
        guard str(o, "algorithm") == algorithm else { throw Failure("unsupported signature algorithm") }
        guard str(o, "channel") == channel else {
            throw Failure("this signature is for \(str(o, "channel") ?? "null"), not \(channel)")
        }
        guard let manifest = o["manifest"] else { throw Failure("the signed list carried no list") }
        guard let publicKey = unb64(str(o, "public_key")) else { throw Failure("the signed list is malformed") }
        let actual = fingerprintOf(publicKey)
        guard actual == expected else { throw Failure("the list was signed by \(actual), not \(expected)") }
        guard let issuedAt = wholeNumber(o["issued_at"]), let serial = wholeNumber(o["serial"]), issuedAt >= 0, serial >= 0 else {
            throw Failure("the signed list carries no issue time or serial: upgrade the Domovoi server first")
        }
        guard let signature = unb64(str(o, "signature_v2")) else {
            throw Failure("the signed list carries no signature over its serial: upgrade the Domovoi server first")
        }
        let message = try manifestMessageV2(channel: channel, manifest: manifest, issuedAt: issuedAt, serial: serial)
        guard Ed25519Verify.verify(publicKey: publicKey, message: message, signature: signature) else {
            throw Failure("the list's signature did not verify")
        }
        guard case .object(let entries) = manifest else { throw Failure("the list is not a path -> sha256 map") }
        var files: [String: String] = [:]
        for (path, value) in entries {
            guard case .string(let sha) = value else { throw Failure("the list is not a path -> sha256 map") }
            files[path] = sha
        }
        return RoomSignedList(manifest: files, serial: serial, issuedAt: issuedAt, digest: try manifestDigest(manifest))
    }

    /// Refuse an envelope older than `last` (`check_manifest_freshness`): a
    /// smaller serial, or the same serial for a different list. A newer
    /// serial is always taken.
    public static func checkFreshness(_ accepted: RoomSignedList, last: RoomSeenList?) throws {
        guard let last else { return }
        if accepted.serial < last.serial {
            throw Failure(
                "the signed list (serial \(accepted.serial)) is older than the one this phone already accepted (serial \(last.serial))"
            )
        }
        if accepted.serial == last.serial, let digest = last.digest, digest != accepted.digest {
            throw Failure("the signed list reuses serial \(accepted.serial) for a different list")
        }
    }

    /// `MANIFEST_CONTEXT_V2 \n canonical({channel, issued_at, manifest, serial})`.
    public static func manifestMessageV2(channel: String, manifest: RoomJSON, issuedAt: Int64, serial: Int64) throws -> Data {
        let signed = RoomJSON.object([
            "channel": .string(channel),
            "issued_at": .int(issuedAt),
            "manifest": manifest,
            "serial": .int(serial),
        ])
        return Data((manifestContextV2 + "\n").utf8) + (try canonicalJSON(signed))
    }

    public static func manifestDigest(_ manifest: RoomJSON) throws -> String {
        hex(Data(SHA256.hash(data: try canonicalJSON(manifest))))
    }

    /// Python's `json.dumps(doc, sort_keys=True, separators=(",", ":"),
    /// ensure_ascii=False).encode()`: keys in code-point order, no spaces,
    /// non-ASCII as itself, only `"` `\` and control characters escaped.
    /// Whole numbers only (a signed list never carries a float, and Python's
    /// float spelling is not worth matching): a fraction throws.
    public static func canonicalJSON(_ element: RoomJSON) throws -> Data {
        var out = ""
        try write(element, into: &out)
        return Data(out.utf8)
    }

    private static func write(_ e: RoomJSON, into out: inout String) throws {
        switch e {
        case .null: out += "null"
        case .bool(let b): out += b ? "true" : "false"
        case .int(let n): out += String(n)
        case .double:
            throw Failure("a signed list carries a number this phone cannot spell like the server")
        case .string(let s): RoomJSON.writeString(s, into: &out)
        case .array(let items):
            out += "["
            for (i, item) in items.enumerated() {
                if i > 0 { out += "," }
                try write(item, into: &out)
            }
            out += "]"
        case .object(let fields):
            out += "{"
            for (i, key) in fields.keys.sorted(by: RoomJSON.codePointOrder).enumerated() {
                if i > 0 { out += "," }
                RoomJSON.writeString(key, into: &out)
                out += ":"
                if let v = fields[key] { try write(v, into: &out) }
            }
            out += "}"
        }
    }

    // MARK: - helpers

    static func hex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }

    static func describe(_ error: Error) -> String {
        if let f = error as? Failure { return f.message }
        if let n = error as? RoomNetworkFailure { return n.message }
        let ns = error as NSError
        return ns.localizedDescription.isEmpty ? String(describing: error) : ns.localizedDescription
    }

    private static func str(_ o: [String: RoomJSON], _ key: String) -> String? {
        if case .string(let s)? = o[key] { return s }
        return nil
    }

    /// An integer that is not a bool and not a float spelling (the reader
    /// keeps those apart already).
    private static func wholeNumber(_ e: RoomJSON?) -> Int64? {
        if case .int(let n)? = e { return n }
        return nil
    }

    private static func unb64(_ value: String?) -> Data? {
        guard var value, !value.isEmpty, value.count <= 256 else { return nil }
        while value.count % 4 != 0 { value += "=" }
        return Data(base64Encoded: value)
    }
}
