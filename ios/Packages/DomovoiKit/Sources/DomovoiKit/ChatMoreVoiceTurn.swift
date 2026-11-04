import Foundation

/// One push-to-talk turn on the wire, and the reply that comes back.
///
/// Ported from `VoiceTurn.kt` — `VoiceTurnReply` (`:53`), `wavBytes` (`:70`),
/// `voiceRefusal` (`:104`), `sendVoiceTurn` (`:156`) — and `parseWav`
/// (`voice/ReplyPlayer.kt:34`), read against `chat.py:2429` and
/// `domovoi/phone_voice.py:784` on 2026-11-02.
///
/// ### Why voice is buildable tonight and pictures are not
///
/// `POST /api/chat/threads/{id}/voice` is a **multipart POST with a plain
/// JSON answer**, and the core writes both rows of the turn itself
/// (`phone_voice.py:632-648`). So a spoken message needs nothing of
/// `ChatModel/send(_:)` or `ChatPath.sendBody` — the two files a typed
/// picture would need and this item may not edit (NIGHT-RULES §2).

// MARK: - the core's answer

public struct ChatMoreVoiceAudio: Equatable, Sendable, Decodable {
    public let mime: String?
    public let b64: String?

    public init(mime: String? = nil, b64: String? = nil) {
        self.mime = mime
        self.b64 = b64
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        mime = try values.decodeIfPresent(String.self, forKey: .mime)
        b64 = try values.decodeIfPresent(String.self, forKey: .b64)
    }

    enum CodingKeys: String, CodingKey { case mime, b64 }
}

public struct ChatMoreVoiceSpeaker: Equatable, Sendable, Decodable {
    public let personID: Int64?
    public let name: String?
    public let confidence: Double?
    public let confident: Bool

    public init(personID: Int64? = nil, name: String? = nil, confidence: Double? = nil, confident: Bool = false) {
        self.personID = personID
        self.name = name
        self.confidence = confidence
        self.confident = confident
    }

    enum CodingKeys: String, CodingKey {
        case name, confidence, confident
        case personID = "person_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        personID = try values.decodeIfPresent(Int64.self, forKey: .personID)
        name = try values.decodeIfPresent(String.self, forKey: .name)
        confidence = try values.decodeIfPresent(Double.self, forKey: .confidence)
        confident = try values.decodeIfPresent(Bool.self, forKey: .confident) ?? false
    }
}

public struct ChatMoreVoiceError: Equatable, Sendable, Decodable {
    public let detail: String?
    public let code: String?

    public init(detail: String? = nil, code: String? = nil) {
        self.detail = detail
        self.code = code
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        detail = try values.decodeIfPresent(String.self, forKey: .detail)
        code = try values.decodeIfPresent(String.self, forKey: .code)
    }

    enum CodingKeys: String, CodingKey { case detail, code }
}

/// The core's 200 (`phone_voice.py:784-808`; `VoiceTurn.kt:53`).
///
/// Every field tolerates being missing, as the Kotlin's defaults do. A clip
/// under the core's `MIN_CLIP_SEC` comes back **200 with `heard: false` and
/// `reason: "too_short"`** and no rows (`phone_voice.py:656`), which is why
/// ``heard`` is the first thing a caller asks.
public struct ChatMoreVoiceReply: Equatable, Sendable, Decodable {
    public let heard: Bool
    public let reason: String?
    public let transcript: String
    public let reply: String
    public let audio: ChatMoreVoiceAudio?
    public let speaker: ChatMoreVoiceSpeaker?
    public let expectFollowup: Bool
    public let matchedHandler: String?
    public let matchedPath: String?
    public let sessionID: String?
    public let roomID: String?
    /// The two rows the **core** wrote: the question, then the reply.
    public let messages: [ChatMessage]
    public let error: ChatMoreVoiceError?

    public init(
        heard: Bool = false,
        reason: String? = nil,
        transcript: String = "",
        reply: String = "",
        audio: ChatMoreVoiceAudio? = nil,
        speaker: ChatMoreVoiceSpeaker? = nil,
        expectFollowup: Bool = false,
        matchedHandler: String? = nil,
        matchedPath: String? = nil,
        sessionID: String? = nil,
        roomID: String? = nil,
        messages: [ChatMessage] = [],
        error: ChatMoreVoiceError? = nil
    ) {
        self.heard = heard
        self.reason = reason
        self.transcript = transcript
        self.reply = reply
        self.audio = audio
        self.speaker = speaker
        self.expectFollowup = expectFollowup
        self.matchedHandler = matchedHandler
        self.matchedPath = matchedPath
        self.sessionID = sessionID
        self.roomID = roomID
        self.messages = messages
        self.error = error
    }

    enum CodingKeys: String, CodingKey {
        case heard, reason, transcript, reply, audio, speaker, messages, error
        case expectFollowup = "expect_followup"
        case matchedHandler = "matched_handler"
        case matchedPath = "matched_path"
        case sessionID = "session_id"
        case roomID = "room_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        heard = try values.decodeIfPresent(Bool.self, forKey: .heard) ?? false
        reason = try values.decodeIfPresent(String.self, forKey: .reason)
        transcript = try values.decodeIfPresent(String.self, forKey: .transcript) ?? ""
        reply = try values.decodeIfPresent(String.self, forKey: .reply) ?? ""
        audio = try values.decodeIfPresent(ChatMoreVoiceAudio.self, forKey: .audio)
        speaker = try values.decodeIfPresent(ChatMoreVoiceSpeaker.self, forKey: .speaker)
        expectFollowup = try values.decodeIfPresent(Bool.self, forKey: .expectFollowup) ?? false
        matchedHandler = try values.decodeIfPresent(String.self, forKey: .matchedHandler)
        matchedPath = try values.decodeIfPresent(String.self, forKey: .matchedPath)
        sessionID = try values.decodeIfPresent(String.self, forKey: .sessionID)
        roomID = try values.decodeIfPresent(String.self, forKey: .roomID)
        messages = try values.decodeIfPresent([ChatMessage].self, forKey: .messages) ?? []
        error = try values.decodeIfPresent(ChatMoreVoiceError.self, forKey: .error)
        // `actions` is read and ignored, deliberately: play / pause / next /
        // volume on *this phone's* player. The player is the Music item's
        // model and an app cannot set an iPhone's volume at all
        // (`MPVolumeView` is a control a person drags, not a setter), so at
        // least one of the seven could never work — and half-honouring the
        // set is worse than leaving it, because the reply already says what
        // it did (`VoiceTurn.kt:217-238`, DECISIONS.md).
    }

    /// The reply's WAV, or nil when the server sent none — its text-to-speech
    /// was down (`VoiceTurn.kt:70`).
    public var wav: Data? {
        guard let b64 = audio?.b64, !b64.isEmpty else { return nil }
        // `ignoreUnknownCharacters` because Kotlin's MIME decoder tolerates
        // the line breaks a base64 encoder may fold at, and the core's is
        // Python's `b64encode` today — which does not fold, but a core that
        // changed encoder must not silence the reply.
        return Data(base64Encoded: b64, options: [.ignoreUnknownCharacters])
    }

    /// How this reply is heard (`replyPlayback`, `VoiceTurn.kt:94`).
    ///
    /// Android's `PHONE_VOICE` branch needs a Piper voice downloaded as the
    /// phone's own pick (`voice/PhoneVoice.kt:148`); this app has no voice
    /// catalogue, no downloader, and may not use sherpa-onnx's prebuilt iOS
    /// xcframeworks (espeak-ng, GPL — NIGHT-RULES §1). So the set collapses
    /// to the server's audio or nothing, which is exactly what Android does
    /// on a phone with nothing downloaded. `AVSpeechSynthesizer` is **not**
    /// used for a reply: a system voice reading the words would be a silent
    /// fallback wearing the house's voice.
    public var playback: ChatMoreReplyPlayback {
        wav != nil ? .serverAudio : .none
    }
}

/// How a chat-mic reply is heard (`VoiceTurn.kt:75`, two of its three cases).
public enum ChatMoreReplyPlayback: Equatable, Sendable {
    /// The audio the server rendered, in the house's own voice.
    case serverAudio
    /// Nothing to hear: no text, or the server's text-to-speech was down.
    case none
}

// MARK: - a refused turn

/// A refusal, in the server's own words when it sent any
/// (`VoiceTurnException`, `VoiceTurn.kt:101`).
public struct ChatMoreVoiceRefusal: Equatable, Sendable, Error {
    public let status: Int
    public let text: String
    public let code: String?

    public init(status: Int, text: String, code: String? = nil) {
        self.status = status
        self.text = text
        self.code = code
    }

    /// What the server said a refusal was, or a plain fallback
    /// (`voiceRefusal`, `VoiceTurn.kt:104`).
    ///
    /// **The body's `detail` first.** The core sends its own sentences — "a
    /// voice turn is at most 30 seconds" (`phone_voice.py:166`), "this
    /// phone's last turn is still being answered" (`:658-660`) — and those
    /// are the words Android shows. The three fallbacks below exist for a
    /// server too old to send `detail`, so a test written against them is a
    /// test of the wrong half.
    public static func of(status: Int, body: String) -> ChatMoreVoiceRefusal {
        let object = ChatMoreJSON.object(body)
        let detail = ChatMoreJSON.string(object, "detail")
        let code = ChatMoreJSON.string(object, "code")
        let text: String
        if let detail {
            text = detail
        } else {
            switch status {
            case 409: text = ChatMoreStrings.voiceBusyFallback
            case 413: text = ChatMoreStrings.voiceTooLongFallback
            default: text = ChatMoreStrings.voiceRefusedFallback(status)
            }
        }
        return ChatMoreVoiceRefusal(status: status, text: text, code: code)
    }

    /// The same, from a ``DomovoiError`` the client threw.
    ///
    /// A 401 or 403 is **not** turned into one of these: it is the pairing
    /// question, and the client has already raised it (`VoiceTurn.kt:181`
    /// re-throws those as `ApiException` for the same reason). Nil for
    /// anything that never reached the server.
    public static func of(_ error: DomovoiError) -> ChatMoreVoiceRefusal? {
        guard let failure = error.failure else { return nil }
        guard failure.status != 401, failure.status != 403 else { return nil }
        return of(status: failure.status, body: failure.body)
    }
}

// MARK: - the WAV the reply comes back as

/// Mono or stereo PCM16 and its rate, as a WAV file carries it
/// (`Pcm16`, `ReplyPlayer.kt:23`).
public struct ChatMorePcm16: Equatable, Sendable {
    public let sampleRate: Int
    public let channels: Int
    public let data: Data

    public init(sampleRate: Int, channels: Int, data: Data) {
        self.sampleRate = sampleRate
        self.channels = channels
        self.data = data
    }

    public var seconds: Double {
        guard sampleRate > 0, channels > 0 else { return 0 }
        return Double(data.count) / 2.0 / Double(channels) / Double(sampleRate)
    }
}

/// The PCM16 inside a RIFF/WAVE file — what the core's text-to-speech sends
/// (Piper, or the stub).
///
/// `parseWav` (`ReplyPlayer.kt:34`), ported whole: it **walks the chunks**
/// rather than assuming a 44-byte header, since some writers add a `LIST`
/// chunk, and a `data` chunk whose size claims more than the file holds — a
/// streamed writer's placeholder — is read to the end. Nil for anything else.
public enum ChatMoreWav {

    public static func parse(_ bytes: Data) -> ChatMorePcm16? {
        let all = [UInt8](bytes)
        guard all.count >= 12 else { return nil }
        func ascii(_ from: Int, _ length: Int) -> String? {
            guard from >= 0, from + length <= all.count else { return nil }
            return String(bytes: all[from..<(from + length)], encoding: .ascii)
        }
        /// Four little-endian bytes as the **signed** 32-bit integer the
        /// format defines, assembled unsigned first: a chunk size with its
        /// top bit set is 0x80000000 and up, which would trap on the way into
        /// `Int32` and reads as a negative size — which is the case Kotlin's
        /// `if (size < 0) return null` refuses, and is refused below.
        func int32(_ at: Int) -> Int32? {
            guard at >= 0, at + 4 <= all.count else { return nil }
            let raw = UInt32(all[at]) | UInt32(all[at + 1]) << 8
                | UInt32(all[at + 2]) << 16 | UInt32(all[at + 3]) << 24
            return Int32(bitPattern: raw)
        }
        func int16(_ at: Int) -> Int? {
            guard at >= 0, at + 2 <= all.count else { return nil }
            return Int(all[at]) | Int(all[at + 1]) << 8
        }
        guard ascii(0, 4) == "RIFF", ascii(8, 4) == "WAVE" else { return nil }

        var position = 12
        var rate = 0
        var channels = 0
        var bits = 0
        var format = 0
        while position + 8 <= all.count {
            guard let id = ascii(position, 4), let declared = int32(position + 4) else { return nil }
            let body = position + 8
            if declared < 0 { return nil }
            let size = Int(declared)
            switch id {
            case "fmt ":
                guard body + 16 <= all.count,
                      let f = int16(body), let c = int16(body + 2),
                      let r = int32(body + 4), let b = int16(body + 14) else { return nil }
                format = f
                channels = c
                rate = Int(r)
                bits = b
            case "data":
                // PCM16, mono or stereo, a sane rate — anything else is not
                // what the core sends and is not guessed at.
                guard format == 1, bits == 16, (1...2).contains(channels), rate > 0 else { return nil }
                let end = min(all.count, body &+ size)
                guard end > body else { return nil }
                let frame = 2 * channels
                let length = (end - body) / frame * frame
                guard length > 0 else { return nil }
                return ChatMorePcm16(
                    sampleRate: rate,
                    channels: channels,
                    data: Data(all[body..<(body + length)])
                )
            default:
                break
            }
            // Chunks are word-aligned.
            let next = body + size + (size & 1)
            // A chunk that did not move the cursor forward would spin here —
            // a zero-size chunk is legal and must still advance by its eight
            // header bytes, which `body` already is, and nothing may go
            // backwards.
            guard next > position else { return nil }
            position = next
        }
        return nil
    }
}

// MARK: - sending one

public enum ChatMoreVoiceTurn {

    /// 16 kHz mono PCM16, the rate the core's speech-to-text takes
    /// (`VOICE_SAMPLE_RATE`, `HoldToTalk.kt:30`; `phone_voice.py:97`).
    public static let sampleRate = 16_000

    /// The longest turn the core takes (`MAX_CLIP_SEC`,
    /// `phone_voice.py:100`; `HoldToTalk.kt:151`).
    public static let maxSeconds = 30.0

    /// Shorter than this is a tap, not speech (`MIN_CLIP_SEC`,
    /// `phone_voice.py:103`; `HoldToTalk.kt:154`).
    public static let minSeconds = 0.3

    /// 32 ms at 16 kHz, the assistant spike's chunk (`HoldToTalk.kt:157`).
    public static let chunkSamples = 512

    /// How long the server may take: speech-to-text, the router (a cold model
    /// can take most of a minute on an all-CPU box) and text-to-speech. Above
    /// the web hop's own 180 s so the server's 504 is what the phone reads
    /// (`VOICE_READ_TIMEOUT_SEC`, `VoiceTurn.kt:141`).
    public static let readTimeout: TimeInterval = 200

    /// Send one recording into `thread`.
    ///
    /// `speak` is never sent as false: see ``ChatMoreVoiceReply/playback``.
    /// `thread_id` is **not** in the form — the server adds it from the path
    /// (`chat.py:2456`); `phone_id` **is** required by it, so it is a plain
    /// `String` here and the caller does not offer the turn without one.
    ///
    /// Throws a ``ChatMoreVoiceRefusal`` for a refusal the server worded, and
    /// the ``DomovoiError`` itself for anything else (a 401, a held identity,
    /// no route to the house) — the two go to different places on screen, as
    /// `VoiceTurn.kt:179-188` sends them.
    public static func send(
        using client: DomovoiClient,
        thread: Int64,
        pcm: Data,
        phoneID: String,
        player: String? = nil
    ) async throws -> ChatMoreVoiceReply {
        var form = ChatMoreMultipart()
        form.add(name: "audio", filename: "turn.pcm", bytes: pcm)
        // **Not optional.** `phone_id: str = Form(..., max_length=64)`
        // (`chat.py:2437`) is required, and a form without it came back 422
        // with FastAPI's validation *array* as `detail` — which
        // ``ChatMoreVoiceRefusal/of(status:body:)`` cannot word, so a
        // recording was taken and thrown away under a generic sentence. The
        // caller refuses the turn instead (``ChatMoreModel/talk(pcm:interrupted:)``).
        form.add(name: "phone_id", value: phoneID)
        // The phone's own player state, which this app does not report: the
        // player is the Music item's, and "what's playing" answered about a
        // player this file cannot see would be an invented answer.
        if let player, !player.isEmpty { form.add(name: "player", value: player) }

        let answer: Data
        // `throws(DomovoiError)` on the `do` itself: a bare `do` is typed
        // `any Error` (`ChatModel.swift:574`), and ``ChatMoreVoiceRefusal/of(_:)``
        // takes the one type.
        do throws(DomovoiError) {
            answer = try await client.raw(
                method: "POST",
                path: ChatPath.voice(thread: thread),
                body: form.body,
                contentType: form.contentType,
                timeout: readTimeout
            )
        } catch {
            if let refusal = ChatMoreVoiceRefusal.of(error) { throw refusal }
            throw error
        }
        return try DomovoiClient.decode(answer, as: ChatMoreVoiceReply.self)
    }
}
