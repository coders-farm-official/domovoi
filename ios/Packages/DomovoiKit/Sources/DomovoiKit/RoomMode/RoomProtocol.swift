import Foundation

/// The room phone's side of the satellite protocol on `/v1/stream/{room}`
/// (docs/uml/satellite-protocol.md; the core's contract comment at the top of
/// domovoi/streaming.py) — `roommode/RoomProtocol.kt`. Frames only, no I/O:
/// what the phone says, and what it makes of what the core says. Held to the
/// shared fixtures (docs/uml/satellite-fixtures.json) by `RoomProtocolTests`,
/// which the core's and the Pi's tests read too.
public enum RoomProtocol {
    public static let protocolVersion = "0.1"

    /// What the hello names the device (`satellites.board`, the dashboard's
    /// badge). An iPhone is not an Android room (ROOMMODE-MAP D5).
    public static let board = "ios_room"

    public static let frameSamples = RoomAudioFrame.samples
    public static let frameBytes = RoomAudioFrame.bytes
    public static let frameMS = RoomAudioFrame.milliseconds
    public static let sampleRate = RoomAudioFrame.sampleRate

    /// A frame the room sends or reads: one JSON object.
    public typealias Frame = [String: RoomJSON]

    /// Exactly what this client handles, as the hello's `supports` list
    /// (domovoi/satellite_abilities.py): a device that sends the list gets
    /// nothing it did not name. Never chat mode. `full_duplex` only with an
    /// echo canceller (an iPhone always has voice processing): it is what
    /// lets the core start a drop-in here. `dropin_ready` goes with it
    /// (``DropinAfterReply``). `RoomProtocol.kt:46-54`.
    public static func supports(echoCanceller: Bool) -> [String] {
        var out: [String] = []
        if echoCanceller { out.append("full_duplex") }
        out.append("speech_pause")
        out.append("capture_control")
        out.append("announce_after_session_end")
        out.append("wake_models")
        out.append("sounds")
        if echoCanceller { out.append("dropin_ready") }
        return out
    }

    /// `RoomProtocol.kt:56-73`.
    public static func hello(
        roomID: String,
        wakeWord: String,
        pairingToken: String,
        approvalCode: String?,
        echoCanceller: Bool,
        micEnabled: Bool = true
    ) -> Frame {
        [
            "type": .string("hello"),
            "room_id": .string(roomID),
            "wake_word": .string(wakeWord),
            "pairing_token": .string(pairingToken),
            "approval_code": approvalCode.map { .string($0) } ?? .null,
            "sat_type": .string("voice"),
            "mic_enabled": .bool(micEnabled),
            "board": .string(board),
            "supports": .array(supports(echoCanceller: echoCanceller).map { .string($0) }),
        ]
    }

    // MARK: - what the phone sends

    /// `wakeScore`: the wake's peak score (`WakeGate.lastPeak`), sent as
    /// `wake_score` (three decimals, as the Pi does) on a wake-word capture
    /// only; the core keeps it on the turn's history row (V044).
    /// `RoomProtocol.kt:80-89`.
    public static func utteranceStart(trigger: String, utt: Int, backlogMS: Int, wakeMS: Int?, wakeScore: Float? = nil) -> Frame {
        var f: Frame = [
            "type": .string("utterance_start"),
            "trigger": .string(trigger),
            "utt": .int(Int64(utt)),
            "backlog_ms": .int(Int64(backlogMS)),
        ]
        if let wakeMS { f["wake_ms"] = .int(Int64(wakeMS)) }
        if trigger == "wake_word", let wakeScore, wakeScore.isFinite {
            let clamped = min(1.0, max(0.0, Double(wakeScore)))
            f["wake_score"] = .double((clamped * 1000).rounded() / 1000)
        }
        return f
    }

    /// The room's effective wake settings as `config_status` (flat
    /// `section.key` map, domovoi/satellite_config_schema.py): the threshold
    /// the room wakes at, and the wake gate's patience and cooldown. Sent
    /// after `ready` and after a pushed wake word changes the threshold.
    /// `RoomProtocol.kt:99-107`.
    public static func configStatus(threshold: Float, patienceFrames: Int, cooldownSec: Double) -> Frame {
        [
            "type": .string("config_status"),
            "config": .object([
                // Four decimals, as the Pi stores a threshold: 0.6 reads 0.6.
                "wake.threshold": .double((Double(threshold) * 10_000).rounded() / 10_000),
                "wake.patience_frames": .int(Int64(patienceFrames)),
                "wake.cooldown_sec": .double(cooldownSec),
            ]),
        ]
    }

    public static func volumeStatus(level: Int) -> Frame {
        ["type": .string("volume_status"), "level": .int(Int64(min(100, max(0, level))))]
    }

    public static func musicFailed(streamURL: String, reason: String, attempts: Int) -> Frame {
        [
            "type": .string("music_failed"),
            "stream_url": .string(String(streamURL.prefix(300))),
            "reason": .string(String(reason.prefix(200))),
            "attempts": .int(Int64(attempts)),
        ]
    }

    public static let musicReady: Frame = ["type": .string("music_ready")]
    public static let dropinEnd: Frame = ["type": .string("dropin_end")]
    /// Only in answer to a `dropin_start` marked `await_ready`.
    public static let dropinReady: Frame = ["type": .string("dropin_ready")]

    /// A message type an older core would answer with `error` is sent only
    /// when its `ready.features` lists it (frame -> feature).
    public static let gatedTypes: [String: String] = [
        "speech_pause": "speech_pause",
        "speech_resume": "speech_pause",
        "music_failed": "music_failed",
        "health": "health",
        "log_push": "log_push",
    ]

    /// Whether `frame` may go to a core whose `ready` listed `features`.
    public static func maySend(_ frame: Frame, features: Set<String>) -> Bool {
        guard case .string(let type)? = frame["type"] else { return false }
        guard let gate = gatedTypes[type] else { return true }
        return features.contains(gate)
    }

    /// `http(s)://h:p` -> `ws(s)://h:p/v1/stream/<room>`.
    public static func streamURL(coreURL: String, roomID: String) -> String {
        var base = coreURL
        while base.hasSuffix("/") { base.removeLast() }
        let ws: String
        if base.hasPrefix("https://") {
            ws = "wss://" + base.dropFirst("https://".count)
        } else if base.hasPrefix("http://") {
            ws = "ws://" + base.dropFirst("http://".count)
        } else {
            ws = base
        }
        return ws + "/v1/stream/" + roomID
    }

    // MARK: - what the core sends

    /// `RoomProtocol.kt:156-197`.
    public enum ServerFrame: Equatable, Sendable {
        /// `roomVoice`: the voice the server keeps for this room (`room_voice`;
        /// nil: the server's own). This device keeps no voice choice.
        case ready(features: Set<String>, roomID: String?, botName: String?, roomVoice: String?)
        case error(reason: String?, message: String?, code: String?)
        case pong
        case transcript(text: String)
        case responseStart(text: String?, sampleRate: Int)
        /// `ignored`: the core took the capture for background talk.
        case responseEnd(interrupted: Bool, expectFollowup: Bool, ignored: Bool)
        case setVolume(level: Int)
        case musicStart(streamURL: String?)
        case musicStop
        case endCapture(utt: Int?)
        /// `saysVoice`: the frame carried `room_voice`.
        case soundsChanged(roomVoice: String?, saysVoice: Bool)
        case setWakeWord(slug: String?, threshold: Double?)
        case wakeModelsChanged
        /// `awaitReady`: finish the reply still playing, then open the call
        /// and say `dropin_ready`.
        case dropinStart(peerRoom: String?, peerLabel: String?, sampleRate: Int, fullDuplex: Bool, awaitReady: Bool)
        case dropinEnd(reason: String?)
        /// A type this client does not handle (logged, ignored).
        case other(type: String?)
    }

    /// One text frame from the core; ``ServerFrame/other(type:)`` for anything
    /// else. `RoomProtocol.kt:200-231`.
    public static func parse(_ text: String) -> ServerFrame {
        guard case .object(let o)? = RoomJSON.parse(text) else { return .other(type: nil) }
        func s(_ k: String) -> String? {
            if case .string(let v)? = o[k] { return v }
            return nil
        }
        func i(_ k: String) -> Int? {
            switch o[k] {
            case .int(let n)?: return Int(clamping: n)
            case .double(let d)? where d.isFinite && abs(d) < 1e15: return Int(d)
            default: return nil
            }
        }
        func b(_ k: String) -> Bool {
            if case .bool(let v)? = o[k] { return v }
            return false
        }
        func voice(_ k: String) -> String? {
            guard let v = s(k), !v.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
            return v
        }
        switch s("type") {
        case "ready":
            var features: Set<String> = []
            if case .array(let items)? = o["features"] {
                for item in items { if case .string(let f) = item { features.insert(f) } }
            }
            return .ready(features: features, roomID: s("room_id"), botName: s("bot_name"), roomVoice: voice("room_voice"))
        case "error":
            return .error(reason: s("reason"), message: s("message"), code: s("code"))
        case "pong":
            return .pong
        case "transcript":
            return .transcript(text: s("text") ?? "")
        case "response_start":
            return .responseStart(text: s("text"), sampleRate: min(48_000, max(8_000, i("audio_sample_rate") ?? sampleRate)))
        case "response_end":
            return .responseEnd(interrupted: b("interrupted"), expectFollowup: b("expect_followup"), ignored: b("ignored"))
        case "set_volume":
            return .setVolume(level: min(100, max(0, i("level") ?? 50)))
        case "music_start":
            return .musicStart(streamURL: s("stream_url"))
        case "music_stop":
            return .musicStop
        case "end_capture":
            return .endCapture(utt: i("utt"))
        case "sounds_changed":
            return .soundsChanged(roomVoice: voice("room_voice"), saysVoice: o["room_voice"] != nil)
        case "set_wake_word":
            let threshold: Double?
            switch o["threshold"] {
            case .int(let n)?: threshold = Double(n)
            case .double(let d)?: threshold = d
            default: threshold = nil
            }
            return .setWakeWord(slug: s("slug"), threshold: threshold)
        case "wake_models_changed":
            return .wakeModelsChanged
        case "dropin_start":
            return .dropinStart(
                peerRoom: s("peer_room"), peerLabel: s("peer_label"),
                sampleRate: i("audio_sample_rate") ?? sampleRate,
                fullDuplex: b("full_duplex"), awaitReady: b("await_ready")
            )
        case "dropin_end":
            return .dropinEnd(reason: s("reason"))
        case let type:
            return .other(type: type)
        }
    }

    /// Only an http(s) URL with no whitespace or control characters is a
    /// stream (the Pi's `_is_stream_url`).
    public static func isStreamURL(_ value: String?) -> Bool {
        guard let value, !value.isEmpty else { return false }
        guard value.hasPrefix("http://") || value.hasPrefix("https://") else { return false }
        return !value.unicodeScalars.contains { s in
            s.properties.isWhitespace || s.value < 0x20 || s.value == 0x7F
        }
    }

    /// The frame's text on the wire.
    public static func text(_ frame: Frame) -> String {
        RoomJSON.object(frame).serialized
    }
}

// MARK: - reading JSON into RoomJSON

extension RoomJSON {

    /// The value at `key` when this is an object.
    public subscript(key: String) -> RoomJSON? {
        if case .object(let o) = self { return o[key] }
        return nil
    }

    /// The text when this is a string.
    public var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }

    /// Parses one JSON document, keeping a whole number (no `.`, `e` or `E`
    /// in its spelling) apart from a fraction — what a signed list's
    /// canonical form and the core's integers need, and what
    /// `JSONSerialization`'s `NSNumber` blurs. Nil for anything that is not
    /// exactly one JSON value.
    public static func parse(_ text: String) -> RoomJSON? {
        var reader = RoomJSONReader(Array(text.utf8))
        guard let value = reader.value(depth: 0) else { return nil }
        reader.skipSpace()
        return reader.atEnd ? value : nil
    }

    public static func parse(_ data: Data) -> RoomJSON? {
        guard let text = String(data: data, encoding: .utf8) else { return nil }
        return parse(text)
    }
}

/// A small, strict JSON reader (RFC 8259) over UTF-8 bytes.
struct RoomJSONReader {
    private let bytes: [UInt8]
    private var at = 0
    private static let maxDepth = 64

    init(_ bytes: [UInt8]) {
        self.bytes = bytes
    }

    var atEnd: Bool { at >= bytes.count }

    mutating func skipSpace() {
        while at < bytes.count, [0x20, 0x09, 0x0A, 0x0D].contains(bytes[at]) { at += 1 }
    }

    mutating func value(depth: Int) -> RoomJSON? {
        guard depth < Self.maxDepth else { return nil }
        skipSpace()
        guard at < bytes.count else { return nil }
        switch bytes[at] {
        case UInt8(ascii: "{"): return object(depth: depth)
        case UInt8(ascii: "["): return array(depth: depth)
        case UInt8(ascii: "\""): return string().map { .string($0) }
        case UInt8(ascii: "t"): return literal("true", .bool(true))
        case UInt8(ascii: "f"): return literal("false", .bool(false))
        case UInt8(ascii: "n"): return literal("null", .null)
        default: return number()
        }
    }

    private mutating func literal(_ word: String, _ value: RoomJSON) -> RoomJSON? {
        let w = Array(word.utf8)
        guard at + w.count <= bytes.count, Array(bytes[at..<(at + w.count)]) == w else { return nil }
        at += w.count
        return value
    }

    private mutating func object(depth: Int) -> RoomJSON? {
        at += 1
        var out: [String: RoomJSON] = [:]
        skipSpace()
        if at < bytes.count, bytes[at] == UInt8(ascii: "}") {
            at += 1
            return .object(out)
        }
        while true {
            skipSpace()
            guard at < bytes.count, bytes[at] == UInt8(ascii: "\""), let key = string() else { return nil }
            skipSpace()
            guard at < bytes.count, bytes[at] == UInt8(ascii: ":") else { return nil }
            at += 1
            guard let v = value(depth: depth + 1) else { return nil }
            out[key] = v
            skipSpace()
            guard at < bytes.count else { return nil }
            if bytes[at] == UInt8(ascii: ",") { at += 1; continue }
            if bytes[at] == UInt8(ascii: "}") { at += 1; return .object(out) }
            return nil
        }
    }

    private mutating func array(depth: Int) -> RoomJSON? {
        at += 1
        var out: [RoomJSON] = []
        skipSpace()
        if at < bytes.count, bytes[at] == UInt8(ascii: "]") {
            at += 1
            return .array(out)
        }
        while true {
            guard let v = value(depth: depth + 1) else { return nil }
            out.append(v)
            skipSpace()
            guard at < bytes.count else { return nil }
            if bytes[at] == UInt8(ascii: ",") { at += 1; continue }
            if bytes[at] == UInt8(ascii: "]") { at += 1; return .array(out) }
            return nil
        }
    }

    private mutating func hex4() -> UInt32? {
        guard at + 4 <= bytes.count else { return nil }
        var v: UInt32 = 0
        for _ in 0..<4 {
            let c = bytes[at]
            at += 1
            v <<= 4
            switch c {
            case UInt8(ascii: "0")...UInt8(ascii: "9"): v |= UInt32(c - UInt8(ascii: "0"))
            case UInt8(ascii: "a")...UInt8(ascii: "f"): v |= UInt32(c - UInt8(ascii: "a") + 10)
            case UInt8(ascii: "A")...UInt8(ascii: "F"): v |= UInt32(c - UInt8(ascii: "A") + 10)
            default: return nil
            }
        }
        return v
    }

    private mutating func string() -> String? {
        at += 1 // the opening quote
        var raw: [UInt8] = []
        while at < bytes.count {
            let c = bytes[at]
            at += 1
            if c == UInt8(ascii: "\"") { return String(bytes: raw, encoding: .utf8) }
            if c < 0x20 { return nil }
            guard c == UInt8(ascii: "\\") else {
                raw.append(c)
                continue
            }
            guard at < bytes.count else { return nil }
            let e = bytes[at]
            at += 1
            switch e {
            case UInt8(ascii: "\""): raw.append(0x22)
            case UInt8(ascii: "\\"): raw.append(0x5C)
            case UInt8(ascii: "/"): raw.append(0x2F)
            case UInt8(ascii: "b"): raw.append(0x08)
            case UInt8(ascii: "f"): raw.append(0x0C)
            case UInt8(ascii: "n"): raw.append(0x0A)
            case UInt8(ascii: "r"): raw.append(0x0D)
            case UInt8(ascii: "t"): raw.append(0x09)
            case UInt8(ascii: "u"):
                guard var code = hex4() else { return nil }
                if (0xD800...0xDBFF).contains(code) {
                    // A surrogate pair: the low half must follow.
                    guard at + 2 <= bytes.count, bytes[at] == UInt8(ascii: "\\"), bytes[at + 1] == UInt8(ascii: "u") else { return nil }
                    at += 2
                    guard let low = hex4(), (0xDC00...0xDFFF).contains(low) else { return nil }
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                } else if (0xDC00...0xDFFF).contains(code) {
                    return nil
                }
                guard let scalar = Unicode.Scalar(code) else { return nil }
                raw.append(contentsOf: Array(String(Character(scalar)).utf8))
            default:
                return nil
            }
        }
        return nil
    }

    private mutating func number() -> RoomJSON? {
        let start = at
        if at < bytes.count, bytes[at] == UInt8(ascii: "-") { at += 1 }
        var digits = 0
        var fraction = false
        while at < bytes.count {
            let c = bytes[at]
            if c >= UInt8(ascii: "0"), c <= UInt8(ascii: "9") {
                digits += 1
            } else if c == UInt8(ascii: ".") || c == UInt8(ascii: "e") || c == UInt8(ascii: "E") {
                fraction = true
            } else if c == UInt8(ascii: "+") || c == UInt8(ascii: "-") {
                guard fraction else { break }
            } else {
                break
            }
            at += 1
        }
        guard digits > 0, let text = String(bytes: bytes[start..<at], encoding: .utf8) else { return nil }
        if !fraction, let n = Int64(text) { return .int(n) }
        guard let d = Double(text), d.isFinite else { return nil }
        return .double(d)
    }
}
