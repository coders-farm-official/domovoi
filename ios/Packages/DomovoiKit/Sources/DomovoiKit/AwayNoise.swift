import CryptoKit
import Foundation

// ===========================================================================
// Away chat's Noise handshake (1.5.0): `Noise_IK_25519_ChaChaPoly_SHA256`,
// written out for the one pattern it needs, on CryptoKit.
// ===========================================================================
//
// Away chat (AWAY-CHAT-WIRE §2.2) runs one Noise IK handshake per connection
// to the house's away onion: the phone is the initiator, with its own Noise
// static key as `s`, and the house's away key (`house_noise_pub`, from the
// enrolment at home) as the responder's static key it already knows. The
// house's side is `domovoi/friend_houses/noise.py`; this is that file's IK
// half, rule for rule, and it is checked against the same published
// cacophony vector (`noise_vectors.json`) plus vectors the Python side wrote
// for away chat's own prologue (`away_noise_interop.json`,
// `ios/scripts/make-away-noise-fixture.py`).
//
// The responder role is here too, because the client's tests need a house to
// talk to without a socket; the app only ever runs the initiator.
//
// Noise spec revision 34. The parts that are easy to get wrong:
//  * the ChaCha20-Poly1305 nonce is 4 zero bytes, then the 64-bit counter
//    LITTLE-endian (§5.1, "ChaChaPoly"); 2^64-1 is reserved and never used;
//  * HKDF is Noise's (§4.3): HMAC-SHA256 with the chaining key as the key,
//    not RFC 5869's salt/info shape;
//  * a protocol name of 32 bytes or fewer is padded with zeros to make `h`,
//    a longer one is hashed (this one is 32 bytes: padded);
//  * a decrypt that fails does not move the counter.

/// A handshake or transport message refused. It never says why in a way a
/// peer could use: the caller drops the connection.
public struct NoiseError: Error, Equatable, Sendable, CustomStringConvertible {
    public let reason: String
    public init(_ reason: String) { self.reason = reason }
    public var description: String { "noise: \(reason)" }
}

/// Noise's sizes.
public enum NoiseSizes {
    public static let dh = 32
    public static let hash = 32
    public static let tag = 16
    /// The largest Noise message (a frame's payload).
    public static let maxMessage = 65_535
}

/// An X25519 key pair as raw bytes. The private half never leaves the type's
/// owner except into the Keychain (``AwayKeyStore``); its description never
/// prints it.
public struct NoiseKeyPair: Sendable, Equatable, CustomStringConvertible {
    public let privateKey: Data
    public let publicKey: Data

    /// From a stored 32-byte private key.
    public init(privateKey: Data) throws(NoiseError) {
        guard privateKey.count == NoiseSizes.dh,
              let key = try? Curve25519.KeyAgreement.PrivateKey(rawRepresentation: privateKey) else {
            throw NoiseError("bad private key")
        }
        self.privateKey = privateKey
        self.publicKey = key.publicKey.rawRepresentation
    }

    /// A fresh pair from the system's random source.
    public static func generate() -> NoiseKeyPair {
        let key = Curve25519.KeyAgreement.PrivateKey()
        // A key CryptoKit just made always reads back.
        return try! NoiseKeyPair(privateKey: key.rawRepresentation)
    }

    public var description: String { "NoiseKeyPair(public: \(publicKey.count) bytes)" }
}

enum NoiseCrypto {

    static func dh(_ privateKey: Data, _ publicKey: Data) throws(NoiseError) -> Data {
        guard publicKey.count == NoiseSizes.dh,
              let mine = try? Curve25519.KeyAgreement.PrivateKey(rawRepresentation: privateKey),
              let theirs = try? Curve25519.KeyAgreement.PublicKey(rawRepresentation: publicKey),
              let shared = try? mine.sharedSecretFromKeyAgreement(with: theirs) else {
            throw NoiseError("bad key")
        }
        let raw = shared.withUnsafeBytes { Data($0) }
        // A low-order point gives all zeros; the Python side refuses it
        // (`cryptography` raises), so this does too, whatever CryptoKit does.
        guard raw.count == NoiseSizes.dh, raw.contains(where: { $0 != 0 }) else { throw NoiseError("bad key") }
        return raw
    }

    static func hash(_ data: Data) -> Data { Data(SHA256.hash(data: data)) }

    static func hmac(_ key: Data, _ data: Data) -> Data {
        Data(HMAC<SHA256>.authenticationCode(for: data, using: SymmetricKey(data: key)))
    }

    /// Noise's HKDF (§4.3), two or three outputs.
    static func hkdf(_ chainingKey: Data, _ ikm: Data, outputs: Int) -> [Data] {
        let temp = hmac(chainingKey, ikm)
        let out1 = hmac(temp, Data([0x01]))
        let out2 = hmac(temp, out1 + Data([0x02]))
        if outputs == 2 { return [out1, out2] }
        return [out1, out2, hmac(temp, out2 + Data([0x03]))]
    }

    /// 4 zero bytes, then the counter as 8 little-endian bytes.
    static func nonce(_ n: UInt64) -> Data {
        var data = Data(count: 4)
        withUnsafeBytes(of: n.littleEndian) { data.append(contentsOf: $0) }
        return data
    }
}

/// One direction of an encrypted channel: a key and a message counter.
public struct NoiseCipherState: Sendable {
    private var key: Data?
    private(set) var counter: UInt64 = 0
    static let reserved = UInt64.max

    init(key: Data? = nil) { self.key = key }

    var hasKey: Bool { key != nil }

    mutating func encrypt(ad: Data, _ plaintext: Data) throws(NoiseError) -> Data {
        guard let key else { return plaintext }
        guard counter < Self.reserved else { throw NoiseError("channel exhausted") }
        let nonce = NoiseCrypto.nonce(counter)
        guard let n = try? ChaChaPoly.Nonce(data: nonce),
              let box = try? ChaChaPoly.seal(plaintext, using: SymmetricKey(data: key), nonce: n, authenticating: ad) else {
            throw NoiseError("encrypt failed")
        }
        counter += 1
        return Data([UInt8](box.ciphertext) + [UInt8](box.tag))
    }

    mutating func decrypt(ad: Data, _ ciphertext: Data) throws(NoiseError) -> Data {
        guard let key else { return ciphertext }
        guard counter < Self.reserved else { throw NoiseError("channel exhausted") }
        guard ciphertext.count >= NoiseSizes.tag else { throw NoiseError("message refused") }
        let bytes = [UInt8](ciphertext)
        let body = Data(bytes[..<(bytes.count - NoiseSizes.tag)])
        let tag = Data(bytes[(bytes.count - NoiseSizes.tag)...])
        guard let n = try? ChaChaPoly.Nonce(data: NoiseCrypto.nonce(counter)),
              let box = try? ChaChaPoly.SealedBox(nonce: n, ciphertext: body, tag: tag),
              let plain = try? ChaChaPoly.open(box, using: SymmetricKey(data: key), authenticating: ad) else {
            // Only a successful decrypt moves the counter (§5.1).
            throw NoiseError("message refused")
        }
        counter += 1
        return Data([UInt8](plain))
    }
}

struct NoiseSymmetricState: Sendable {
    var h: Data
    var ck: Data
    var cipher = NoiseCipherState()

    init(protocolName: String) {
        let name = Data(protocolName.utf8)
        h = name.count <= NoiseSizes.hash ? name + Data(count: NoiseSizes.hash - name.count) : NoiseCrypto.hash(name)
        ck = h
    }

    mutating func mixKey(_ ikm: Data) {
        let out = NoiseCrypto.hkdf(ck, ikm, outputs: 2)
        ck = out[0]
        cipher = NoiseCipherState(key: out[1])
    }

    mutating func mixHash(_ data: Data) { h = NoiseCrypto.hash(h + data) }

    mutating func encryptAndHash(_ plaintext: Data) throws(NoiseError) -> Data {
        let c = try cipher.encrypt(ad: h, plaintext)
        mixHash(c)
        return c
    }

    mutating func decryptAndHash(_ ciphertext: Data) throws(NoiseError) -> Data {
        let p = try cipher.decrypt(ad: h, ciphertext)
        mixHash(ciphertext)
        return p
    }

    func split() -> (NoiseCipherState, NoiseCipherState) {
        let out = NoiseCrypto.hkdf(ck, Data(), outputs: 2)
        return (NoiseCipherState(key: out[0]), NoiseCipherState(key: out[1]))
    }
}

/// One side of a `Noise_IK_25519_ChaChaPoly_SHA256` handshake.
///
/// ```
/// IK:
///   <- s
///   ...
///   -> e, es, s, ss
///   <- e, ee, se
/// ```
///
/// The initiator must be given the responder's static key; the responder
/// learns the initiator's from the first message (``remoteStatic``) and must
/// check it before acting on the payload. `ephemeral` exists for the test
/// vectors only.
public struct NoiseIK: Sendable {

    public static let protocolName = "Noise_IK_25519_ChaChaPoly_SHA256"

    public let initiator: Bool
    private let s: NoiseKeyPair
    private var e: NoiseKeyPair?
    public private(set) var remoteStatic: Data?
    private var re: Data?
    private var ss: NoiseSymmetricState
    private var step = 0
    private var result: (NoiseCipherState, NoiseCipherState)?

    public init(
        initiator: Bool,
        prologue: Data,
        staticKey: NoiseKeyPair,
        remoteStatic: Data? = nil,
        ephemeral: NoiseKeyPair? = nil
    ) throws(NoiseError) {
        if initiator {
            guard let remoteStatic, remoteStatic.count == NoiseSizes.dh else {
                throw NoiseError("the initiator must know the responder's static key")
            }
        }
        self.initiator = initiator
        self.s = staticKey
        self.e = ephemeral
        self.remoteStatic = initiator ? remoteStatic : nil
        var state = NoiseSymmetricState(protocolName: Self.protocolName)
        state.mixHash(prologue)
        // The pre-message: the responder's static key.
        state.mixHash(initiator ? remoteStatic! : staticKey.publicKey)
        self.ss = state
    }

    public var complete: Bool { result != nil }

    public var handshakeHash: Data { ss.h }

    private var myTurn: Bool { (step % 2 == 0) == initiator }

    private func dh(_ token: String) throws(NoiseError) -> Data {
        switch token {
        case "ee":
            guard let e, let re else { throw NoiseError("out of order") }
            return try NoiseCrypto.dh(e.privateKey, re)
        case "ss":
            guard let rs = remoteStatic else { throw NoiseError("out of order") }
            return try NoiseCrypto.dh(s.privateKey, rs)
        case "es":
            if initiator {
                guard let e, let rs = remoteStatic else { throw NoiseError("out of order") }
                return try NoiseCrypto.dh(e.privateKey, rs)
            }
            guard let re else { throw NoiseError("out of order") }
            return try NoiseCrypto.dh(s.privateKey, re)
        default: // "se": the initiator's static with the responder's ephemeral
            if initiator {
                guard let re else { throw NoiseError("out of order") }
                return try NoiseCrypto.dh(s.privateKey, re)
            }
            guard let e, let rs = remoteStatic else { throw NoiseError("out of order") }
            return try NoiseCrypto.dh(e.privateKey, rs)
        }
    }

    private static let messages: [[String]] = [["e", "es", "s", "ss"], ["e", "ee", "se"]]

    private mutating func finishIfDone() {
        step += 1
        if step == Self.messages.count { result = ss.split() }
    }

    public mutating func writeMessage(_ payload: Data = Data()) throws(NoiseError) -> Data {
        guard !complete, myTurn else { throw NoiseError("out of turn") }
        var out = Data()
        for token in Self.messages[step] {
            switch token {
            case "e":
                if e == nil { e = NoiseKeyPair.generate() }
                let pub = e!.publicKey
                out += pub
                ss.mixHash(pub)
            case "s":
                out += try ss.encryptAndHash(s.publicKey)
            default:
                ss.mixKey(try dh(token))
            }
        }
        out += try ss.encryptAndHash(payload)
        guard out.count <= NoiseSizes.maxMessage else { throw NoiseError("message too large") }
        finishIfDone()
        return out
    }

    public mutating func readMessage(_ message: Data) throws(NoiseError) -> Data {
        guard !complete, !myTurn else { throw NoiseError("out of turn") }
        guard message.count <= NoiseSizes.maxMessage else { throw NoiseError("message too large") }
        let bytes = [UInt8](message)
        var pos = 0
        for token in Self.messages[step] {
            switch token {
            case "e":
                guard bytes.count - pos >= NoiseSizes.dh else { throw NoiseError("message too short") }
                re = Data(bytes[pos..<pos + NoiseSizes.dh])
                pos += NoiseSizes.dh
                ss.mixHash(re!)
            case "s":
                let size = NoiseSizes.dh + (ss.cipher.hasKey ? NoiseSizes.tag : 0)
                guard bytes.count - pos >= size else { throw NoiseError("message too short") }
                remoteStatic = try ss.decryptAndHash(Data(bytes[pos..<pos + size]))
                pos += size
            default:
                ss.mixKey(try dh(token))
            }
        }
        let payload = try ss.decryptAndHash(Data(bytes[pos...]))
        finishIfDone()
        return payload
    }

    /// The two encrypted directions, once the handshake is complete.
    public func transport() throws(NoiseError) -> NoiseTransport {
        guard let (c1, c2) = result else { throw NoiseError("handshake not complete") }
        return initiator
            ? NoiseTransport(send: c1, recv: c2, handshakeHash: ss.h)
            : NoiseTransport(send: c2, recv: c1, handshakeHash: ss.h)
    }
}

/// The encrypted channel after a handshake: one counter per direction.
public struct NoiseTransport: Sendable {
    private var send: NoiseCipherState
    private var recv: NoiseCipherState
    public let handshakeHash: Data

    init(send: NoiseCipherState, recv: NoiseCipherState, handshakeHash: Data) {
        self.send = send
        self.recv = recv
        self.handshakeHash = handshakeHash
    }

    public mutating func encrypt(_ plaintext: Data) throws(NoiseError) -> Data {
        guard plaintext.count + NoiseSizes.tag <= NoiseSizes.maxMessage else { throw NoiseError("message too large") }
        return try send.encrypt(ad: Data(), plaintext)
    }

    public mutating func decrypt(_ ciphertext: Data) throws(NoiseError) -> Data {
        guard ciphertext.count <= NoiseSizes.maxMessage else { throw NoiseError("message too large") }
        return try recv.decrypt(ad: Data(), ciphertext)
    }
}
