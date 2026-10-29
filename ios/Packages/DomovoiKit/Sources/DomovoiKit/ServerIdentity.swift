import CryptoKit
import Foundation

/// The server's cryptographic identity, as this phone checks it.
///
/// A port of `android/.../net/ServerIdentity.kt`, the pure half of the
/// identity layer: what a proof is, and whether a given `/api/health` answer
/// is one. ``IdentityGate`` is the policy half — the cache, the pin book and
/// the verdict the token decision reads.
///
/// The core has a long-lived Ed25519 key (`domovoi/server_identity.py`). Its
/// `/v1/health?challenge=<nonce>` answer carries the public key, its
/// fingerprint (`SHA256:<base64 of sha256(key)>`, the string the dashboard's
/// Settings → About & updates shows and a prepared satellite is baked with)
/// and a signature over `domovoi-health-v1\n<nonce>`; the web backend passes
/// that block through on `/api/health` (security round 3, A6-03). A recording
/// of an earlier answer signs somebody else's nonce and fails.
///
/// The nine checks below are made **in order**, because each one's failure is
/// a different verdict and the verdicts mean different things to the shell.
public enum ServerIdentity {
    public static let algorithm = "ed25519"
    public static let healthContext = "domovoi-health-v1"
    public static let fingerprintPrefix = "SHA256:"

    /// The longest challenge the core accepts (`CHALLENGE_MAX_LEN`), in bytes
    /// before hex.
    static let challengeBytes = 16

    /// A pinned identity: the key (base64, exactly as the server spelt it)
    /// and the fingerprint a person can read off the dashboard.
    ///
    /// `publicKey` keeps the server's own spelling and not the decoded bytes,
    /// because ``check(healthBody:challenge:pinned:)`` compares the pin with
    /// the answer **as strings**, exactly as the Kotlin does
    /// (`pinned.public_key != keyB64`). Two spellings of one key would read
    /// as a mismatch on both phones; the fingerprint check in step 5 is what
    /// makes that impossible in practice, since it is computed from the
    /// decoded bytes.
    public struct Pin: Equatable, Hashable, Sendable, Codable {
        public let publicKey: String
        public let fingerprint: String

        public init(publicKey: String, fingerprint: String) {
            self.publicKey = publicKey
            self.fingerprint = fingerprint
        }

        /// The Kotlin's `@Serializable data class Pin(val public_key, val
        /// fingerprint)`, whose JSON is what `identity_pins` holds on
        /// Android. The names are kept so the two apps' stored blobs are the
        /// same shape — useful to a person comparing them, and the reason
        /// this is not left to Swift's default synthesis.
        enum CodingKeys: String, CodingKey {
            case publicKey = "public_key"
            case fingerprint
        }
    }

    /// What an answer proved.
    public enum Proof: Equatable, Sendable {
        /// Signed by `pin`'s key over our challenge: the pinned key, or the
        /// key to pin when there was none.
        case verified(pin: Pin)

        /// The answer carried no identity block at all, and the web backend
        /// reached its core (or said nothing about it): a web backend from
        /// before identity.
        case noIdentity

        /// The answer carried no identity block because the web backend could
        /// not ask its core (`domovoi_reachable` false: restarting, updating,
        /// slow). **Not a verdict about who the server is.**
        case coreNotAnswering

        /// An identity block that does not hold up: wrong algorithm, a
        /// fingerprint that is not the key's, our challenge not echoed, a
        /// signature that fails.
        case invalid(reason: String)

        /// A valid proof — of a DIFFERENT key than the one pinned. `seenPin`
        /// is that key, proved over our challenge: what the person is shown
        /// and, only if they say it is theirs, what gets pinned instead.
        case mismatch(expected: String, seen: String, seenPin: Pin?)
    }

    /// 32 lowercase hex characters over 16 random bytes, the shape the core
    /// accepts.
    ///
    /// `SystemRandomNumberGenerator` is documented as cryptographically
    /// secure on Apple platforms (it draws on `arc4random_buf`), which is
    /// what Android gets from `SecureRandom`. A predictable nonce would let a
    /// recorded answer be replayed, so this is not a place for
    /// `Int.random(in:)` on a seeded generator.
    public static func newChallenge() -> String {
        var generator = SystemRandomNumberGenerator()
        var hex = ""
        hex.reserveCapacity(challengeBytes * 2)
        for _ in 0..<challengeBytes {
            hex += String(format: "%02x", UInt8.random(in: 0...255, using: &generator))
        }
        return hex
    }

    /// `SHA256:<base64 of sha256(key)>`, unpadded — the core's shape.
    public static func fingerprintOf(_ publicKey: Data) -> String {
        let digest = Data(SHA256.hash(data: publicKey))
        // Java's `withoutPadding()` encoder. Swift has no unpadded option, so
        // the padding is removed afterwards; base64 padding is only ever a
        // run of `=` at the end, so dropping it cannot touch the payload.
        var text = digest.base64EncodedString()
        while text.hasSuffix("=") { text = String(text.dropLast()) }
        return fingerprintPrefix + text
    }

    /// The bytes the core signs for a health challenge. Must agree with
    /// `server_identity.health_message` byte for byte.
    public static func healthMessage(_ challenge: String) -> Data {
        Data((healthContext + "\n" + challenge).utf8)
    }

    /// Judge a `/api/health` answer (`healthBody`, the JSON text) against
    /// `challenge` and the key this phone has `pinned` for the server (nil
    /// when none yet). Never throws.
    ///
    /// The order is `ServerIdentity.kt`'s, step for step:
    ///
    ///  1. the body is a JSON object;
    ///  2. no `identity` object → `domovoi_reachable == false` gives
    ///     ``Proof/coreNotAnswering``, anything else ``Proof/noIdentity``;
    ///  3. `algorithm == "ed25519"`;
    ///  4. `public_key` is base64, ≤ 256 characters, exactly 32 bytes;
    ///  5. `fingerprint` is that key's;
    ///  6. `challenge` is ours, exactly;
    ///  7. `signature` is base64, exactly 64 bytes;
    ///  8. the signature verifies over ``healthMessage(_:)``;
    ///  9. a pin exists and its key differs → ``Proof/mismatch(expected:seen:seenPin:)``.
    public static func check(healthBody: String?, challenge: String, pinned: Pin?) -> Proof {
        guard let document = jsonObject(healthBody) else {
            return .invalid(reason: "the health answer is not a JSON object")
        }
        guard let identity = document["identity"] as? [String: Any] else {
            // The web backend passes the identity through from its core's
            // /v1/health and leaves it out when that ping failed: that is the
            // core being down, not a server without an identity.
            if jsonBool(document["domovoi_reachable"]) == false { return .coreNotAnswering }
            return .noIdentity
        }
        func field(_ name: String) -> String? { jsonString(identity[name]) }

        guard field("algorithm") == algorithm else { return .invalid(reason: "unknown identity algorithm") }
        guard let keyB64 = field("public_key") else { return .invalid(reason: "no public key") }
        guard let key = decodeBase64(keyB64), key.count == Ed25519Verify.keySize else {
            return .invalid(reason: "malformed public key")
        }
        let fingerprint = fingerprintOf(key)
        guard field("fingerprint") == fingerprint else { return .invalid(reason: "the fingerprint is not the key's") }
        guard field("challenge") == challenge else { return .invalid(reason: "our challenge was not echoed") }
        guard let signature = field("signature").flatMap(decodeBase64),
              signature.count == Ed25519Verify.signatureSize else {
            return .invalid(reason: "malformed signature")
        }
        guard Ed25519Verify.verify(publicKey: key, message: healthMessage(challenge), signature: signature) else {
            return .invalid(reason: "the signature does not verify")
        }
        if let pinned, pinned.publicKey != keyB64 {
            return .mismatch(expected: pinned.fingerprint, seen: fingerprint, seenPin: Pin(publicKey: keyB64, fingerprint: fingerprint))
        }
        return .verified(pin: Pin(publicKey: keyB64, fingerprint: fingerprint))
    }

    /// The identity a server ADVERTISES — an open `/api/health` read with no
    /// challenge, as the picker's probe makes: the key and its fingerprint
    /// when they are well-formed and agree, else nil.
    ///
    /// Unproven: a rogue can advertise the real server's public key, because
    /// a public key is public. But it is what the trust dialog shows, and
    /// what is pinned when the person says yes to it, so that the first proof
    /// has to match THIS key rather than whoever answers first on some
    /// network (map §"Things that surprise people", point 8).
    public static func advertised(algorithm: String?, publicKey: String?, fingerprint: String?) -> Pin? {
        guard algorithm == Self.algorithm, let publicKey, let fingerprint else { return nil }
        guard let key = decodeBase64(publicKey), key.count == Ed25519Verify.keySize else { return nil }
        guard fingerprintOf(key) == fingerprint else { return nil }
        return Pin(publicKey: publicKey, fingerprint: fingerprint)
    }

    /// The identity a health answer advertises, read out of the body —
    /// `Discovery.advertisedIdentity` in the Kotlin. Nil for an older
    /// backend, a body that is not JSON, or a block whose key and fingerprint
    /// do not agree (a fingerprint with no key behind it is nothing to pin).
    public static func advertisedIdentity(healthBody: String?) -> Pin? {
        guard let document = jsonObject(healthBody),
              let identity = document["identity"] as? [String: Any] else { return nil }
        return advertised(
            algorithm: jsonString(identity["algorithm"]),
            publicKey: jsonString(identity["public_key"]),
            fingerprint: jsonString(identity["fingerprint"])
        )
    }

    // ---- the pin book, one per server --------------------------------------

    /// The pin book as it is stored. Pins live in plain storage, not the
    /// vault: a public key and its fingerprint are public, and the dashboard
    /// shows the fingerprint to anyone who can read it.
    /// The two apps' blobs have the same **structure** and not the same
    /// **text**: `JSONEncoder` escapes a forward slash, so a pin key lands
    /// here as `{"http:\/\/x:1":…}` where `kotlinx.serialization` writes
    /// `{"http://x:1":…}`. Both decode to the same book on either side, and
    /// nothing compares the strings. `.withoutEscapingSlashes` would make
    /// them identical and is deliberately NOT set: it would change the shape
    /// of every pin book already written by this app for a cosmetic gain.
    /// Flagged rather than fixed, in case a later batch wants a blob that can
    /// be diffed against an Android phone's.
    public static func encodePins(_ pins: [String: Pin]) -> String {
        // Sorted keys so one book has one spelling: the Kotlin's map order is
        // its insertion order, and a blob that changes every write for no
        // reason is a blob a person cannot diff.
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(pins), let text = String(data: data, encoding: .utf8) else { return "{}" }
        return text
    }

    /// Never throws: a corrupt or absent blob means "nothing pinned yet",
    /// and the phone asks each server to prove itself again.
    public static func decodePins(_ raw: String?) -> [String: Pin] {
        guard let raw, let data = raw.data(using: .utf8) else { return [:] }
        return (try? JSONDecoder().decode([String: Pin].self, from: data)) ?? [:]
    }

    // ---- reading the JSON --------------------------------------------------

    /// The body as a JSON object, or nil.
    ///
    /// **Stricter than the Kotlin, in the safe direction.** Android parses
    /// with `DomovoiJson`, which is lenient (`isLenient`, unquoted keys), so
    /// a body that is nearly JSON can still produce an identity block there
    /// and reads as ``Proof/invalid(reason:)`` here. Invalid holds the token;
    /// the strictness can only make iOS refuse where Android admits, never
    /// the reverse. Same reasoning as ``jsonDetail(of:)`` in
    /// `DeviceToken.swift`.
    static func jsonObject(_ text: String?) -> [String: Any]? {
        guard let text, let data = text.data(using: .utf8) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    /// A field read as Kotlin's `jsonPrimitive.contentOrNull` reads it: the
    /// string of a JSON string, and nil for an object, an array or null.
    ///
    /// Kotlin's version also stringifies a number or a boolean (`25519`
    /// becomes `"25519"`), where this returns nil for them. **The two reach
    /// the same refusal in every case the core can produce**, which is a
    /// narrower claim than "no verdict turns on the difference" and is the
    /// true one: every field this is used for is compared against a fixed
    /// string (`"ed25519"`, our hex challenge) or decoded as base64, and
    /// nothing the core emits as a number or a boolean can pass any of those
    /// — so both platforms refuse, iOS one step earlier. In principle a
    /// `public_key` emitted as a bare JSON number whose digits happened to be
    /// the base64 of the pinned 32-byte key would be read by Android and
    /// refused here; the core writes that field as a string and could not
    /// produce it, and the difference is in the safe direction either way.
    /// Checked field by field rather than assumed, and a test pins each one.
    static func jsonString(_ value: Any?) -> String? {
        value as? String
    }

    /// A field read as Kotlin's `booleanOrNull` reads it: a real JSON boolean,
    /// or a JSON string whose content is exactly `true` or `false`
    /// (`content.toBooleanStrictOrNull()` accepts the string form too). Nil
    /// for anything else — a number included, which is why this is not a
    /// plain `as? Bool`.
    ///
    /// `JSONSerialization` bridges both JSON booleans and JSON numbers to
    /// `NSNumber`, and `NSNumber as? Bool` succeeds for `0` and `1`. So
    /// `"domovoi_reachable": 0` would read as `false` here and as nil on
    /// Android — the iOS phone would hold its token where the Android one
    /// sent it. Safe, but a difference for no reason, so the `CFBoolean`
    /// check below keeps the two readings identical.
    static func jsonBool(_ value: Any?) -> Bool? {
        guard let value else { return nil }
        if CFGetTypeID(value as CFTypeRef) == CFBooleanGetTypeID() { return (value as? NSNumber)?.boolValue }
        if let text = value as? String {
            if text == "true" { return true }
            if text == "false" { return false }
        }
        return nil
    }

    /// Java's `Base64.getDecoder().decode()`, with the Kotlin's 256-character
    /// ceiling in front of it.
    ///
    /// **The padding is why this is not one line.** Java's basic decoder
    /// accepts base64 with the trailing `=` left off; `Data(base64Encoded:)`
    /// requires a length that is a multiple of four and returns nil
    /// otherwise, measured by the test beside this. So an unpadded
    /// `public_key` or `signature` would be read by Android and refused by
    /// iOS — the safe direction (the token is held), but a disagreement
    /// about the same server for no good reason. Padding it out first removes
    /// it. One leftover character is invalid base64 to Java as well, so that
    /// case stays a refusal on both.
    static func decodeBase64(_ value: String) -> Data? {
        guard value.count <= 256 else { return nil }
        var text = value
        switch text.count % 4 {
        case 2: text += "=="
        case 3: text += "="
        case 1: return nil
        default: break
        }
        return Data(base64Encoded: text)
    }
}
