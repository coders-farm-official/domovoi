import Foundation

/// What `GET /api/voice` and `GET /api/voice/samples` answer, in the shapes
/// the **server's own route code** states them in — `web/backend/api/voice.py`
/// `VoiceInfo`/`VoiceSettings` (`:68-87`) and `SampleSources` (`:141-142`),
/// built by `domovoi/voice_settings.py:voice_payload` (`:155-169`).
///
/// ### Why these are not Android's decoder
///
/// Android's `VoiceInfo` (`voice/PhoneVoice.kt:53-84`) defaults **every**
/// field — `id = ""`, `name: String? = null`, `server_voice: String? = null`
/// — because it caches the body on disk per server and has to read back a
/// body written by an older build of itself. Nothing here caches, and the
/// route declares all six voice fields and `server_voice` non-optional, so
/// they are decoded non-optional: a shape change then **fails the read**
/// instead of drawing a row of blanks. That is the `ios-stub-house` lesson
/// paid for once already — three of Home's four reads never decoded for a
/// week, and four sections went unaudited because the failure looked like
/// empty data.
///
/// `pronunciations` is the one field that keeps its default, and for a reason
/// that is not tolerance-in-general: the list is the only part of the body a
/// server could plausibly stop sending (it is the owner's, and it may legally
/// be `[]`), and an absent key reading as "no entries" is the same screen as
/// an empty one.
///
/// ## What is not here, and why there is no control for it
///
/// Nothing that describes a voice **on this phone**: no `installed`, no
/// download progress, no `allowMobileData`, no local sample source. iOS has
/// no phonemizer (`ios/Packages/DomovoiPhonemizer/` is empty on purpose), no
/// ONNX Runtime, no model store and nothing that speaks, so every one of
/// those fields would be a value nothing reads. See
/// `design-notes/ios/overnight/voicesettings/DECISIONS.md`.

/// One of the three shipped voices, as the server lists it
/// (`voice_settings.py:128-140`, `shipped_voices`).
///
/// `id` is the Piper **`model_ref`**, which is what
/// `GET /api/voice/sample/{voice_id}` is keyed by (`voice.py:170`), so it is
/// the id a sample asks for and never a display name.
public struct VoiceSettingsVoice: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    /// `"male"` / `"female"`, stated by the server
    /// (`voice_settings.py:134`). Android derives this from the id instead
    /// (`VoicePanel.kt:66-70`: `norman -> "male"`, everything else female);
    /// the output is identical for the three shipped voices, and correct
    /// rather than accidental if a fourth ever appears.
    public let sex: String
    /// The server's own render rate, 22050 for all three.
    public let sampleRate: Int
    /// The size of the `.onnx` a **download** would fetch. Decoded because
    /// the server sends it; deliberately not drawn — see
    /// ``VoiceSettingsStrings`` and DECISIONS, "The download size is not
    /// printed".
    public let sizeBytes: Int64
    public let sha256: String

    private enum CodingKeys: String, CodingKey {
        case id, name, sex, sha256
        case sampleRate = "sample_rate"
        case sizeBytes = "size_bytes"
    }

    public init(
        id: String,
        name: String,
        sex: String,
        sampleRate: Int,
        sizeBytes: Int64,
        sha256: String
    ) {
        self.id = id
        self.name = name
        self.sex = sex
        self.sampleRate = sampleRate
        self.sizeBytes = sizeBytes
        self.sha256 = sha256
    }
}

/// One "say it like" entry: `{"word": "Nguyen", "say_like": "win"}`
/// (`voice.py:79-81`).
public struct VoiceSettingsSayItLike: Decodable, Sendable, Equatable, Identifiable {
    public let word: String
    public let sayLike: String

    /// The word, which is the list's key on the server (`voice_payload`
    /// builds the list from a word-to-say-like mapping, so it is unique) and
    /// so is a stable identity for a row.
    public var id: String { word }

    private enum CodingKeys: String, CodingKey {
        case word
        case sayLike = "say_like"
    }

    public init(word: String, sayLike: String) {
        self.word = word
        self.sayLike = sayLike
    }

    /// Either half blank. `VoiceInfo.pairs()` (`PhoneVoice.kt:74`) drops
    /// these; see ``VoiceSettingsInfo/entries``.
    public var isBlank: Bool {
        word.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || sayLike.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

/// The body of `GET /api/voice` (`voice.py:135-140`).
public struct VoiceSettingsInfo: Decodable, Sendable, Equatable {
    public let voices: [VoiceSettingsVoice]
    /// The voice the **server** speaks with. Non-optional on the route
    /// (`voice.py:85`), and **not necessarily one of ``voices``**: when the
    /// registry's default is not a shipped voice, `server_voice()` returns
    /// `settings.tts_piper_voice`, which can be any voice id at all
    /// (`voice_settings.py:143-152`). So "the house speaks with something
    /// this app does not list" is a reachable state, which is why
    /// ``VoiceSettingsNaming`` has a branch for it instead of substituting a
    /// default the way Android does (`PhoneVoiceChoice.effective`, which
    /// falls back to Kristin because the *phone* has to speak with
    /// something).
    public let serverVoice: String
    /// The **effective** list: the shipped entries with the owner's changes
    /// and removals applied. May legitimately be empty — that means the owner
    /// emptied it, not that the read failed.
    public let pronunciations: [VoiceSettingsSayItLike]
    /// `%Y-%m-%dT%H:%M:%SZ` in UTC (`voice_settings.py:168`). Read with
    /// ``parseInstant(_:)``.
    public let updatedAt: String

    private enum CodingKeys: String, CodingKey {
        case voices
        case serverVoice = "server_voice"
        case pronunciations
        case updatedAt = "updated_at"
    }

    public init(
        voices: [VoiceSettingsVoice],
        serverVoice: String,
        pronunciations: [VoiceSettingsSayItLike] = [],
        updatedAt: String
    ) {
        self.voices = voices
        self.serverVoice = serverVoice
        self.pronunciations = pronunciations
        self.updatedAt = updatedAt
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        voices = try values.decode([VoiceSettingsVoice].self, forKey: .voices)
        serverVoice = try values.decode(String.self, forKey: .serverVoice)
        // The one defaulted key. See the file comment for why this is not
        // tolerance applied across the board.
        pronunciations = try values.decodeIfPresent(
            [VoiceSettingsSayItLike].self, forKey: .pronunciations
        ) ?? []
        updatedAt = try values.decode(String.self, forKey: .updatedAt)
    }

    /// The list with blank entries dropped — `VoiceInfo.pairs()`
    /// (`PhoneVoice.kt:74`). Android drops them before handing the list to
    /// the converter; this drops them before drawing it, so a stray blank
    /// row cannot appear as an entry that says nothing.
    public var entries: [VoiceSettingsSayItLike] {
        pronunciations.filter { !$0.isBlank }
    }

    /// The voice the server speaks with, when it is one this app lists.
    public var houseVoice: VoiceSettingsVoice? {
        voices.first { $0.id == serverVoice }
    }
}

/// What a voice's sample would really be — `piper_sample_source`
/// (`domovoi/voice_sample.py:41-60`), as `GET /api/voice/samples` reports it
/// (`voice.py:147-154`).
public enum VoiceSettingsSampleSource: String, Decodable, Sendable, Equatable {
    /// The server synthesizes it live, in that voice. `audio/wav`.
    case voice
    /// A shipped voice the server has not downloaded: its built-in clip,
    /// served as **Ogg Opus** (`CLIP_MEDIA_TYPE`, `voice_sample.py:22`).
    case clip
}

/// `{"samples": {"<id>": "voice" | "clip" | null}}`.
///
/// This map is the server handing the client the no-misleading-controls
/// predicate, and ``isPlayable(_:)`` is the only rule the play button is
/// allowed to consult.
public struct VoiceSettingsSamples: Decodable, Sendable, Equatable {

    /// Every voice the server answered for. `nil` means "nothing that is
    /// really that voice" — the server's own words — and is kept as a key
    /// with no value rather than dropped, so a caller can tell "the server
    /// said no" from "the server did not mention it".
    public let samples: [String: VoiceSettingsSampleSource?]

    public init(samples: [String: VoiceSettingsSampleSource?]) {
        self.samples = samples
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        // Decoded as `[String: String?]` first, deliberately: mapping the
        // strings afterwards keeps a JSON `null` as a key whose value is
        // nil, and keeps an unknown future source string (`"stream"`, say)
        // from failing the whole read — it reads as "no button", which is
        // the safe side of this particular rule.
        let raw = try values.decode([String: String?].self, forKey: .samples)
        samples = raw.mapValues { text in
            text.flatMap(VoiceSettingsSampleSource.init(rawValue:))
        }
    }

    private enum CodingKeys: String, CodingKey { case samples }

    /// Whether a play button may be drawn for `id`.
    ///
    /// **`.voice` only.** A `.clip` sample arrives as Ogg Opus, which
    /// `AVAudioPlayer` does not decode, so a button that fetched it would
    /// play silence — the exact silent fallback Kamron's rule forbids, and
    /// one Android has already shipped once (`PhoneVoice.kt:371` records
    /// every voice sounding the same when its own TTS answered here). A
    /// voice the server did not mention, or mentioned as `null`, is also no.
    public func isPlayable(_ id: String) -> Bool {
        (samples[id] ?? nil) == .voice
    }

    /// What the server said about `id`; `nil` for both "null" and "not
    /// mentioned". Drawn by nothing — read by the tests that have to tell
    /// the two nos apart.
    public func source(of id: String) -> VoiceSettingsSampleSource? {
        samples[id] ?? nil
    }

    public static let empty = VoiceSettingsSamples(samples: [:])
}

/// How the Voices section names a voice.
public enum VoiceSettingsNaming {

    /// `"Kristin (female)"` — `voiceLabel` (`VoicePanel.kt:65-72`), with the
    /// sex taken from the server's own field rather than from the id.
    public static func label(for voice: VoiceSettingsVoice) -> String {
        let who = voice.sex.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !who.isEmpty else { return voice.name }
        return "\(voice.name) (\(who))"
    }

    /// How the house's voice is named: by name and sex when this app lists
    /// it, and **by the raw id** when it does not.
    ///
    /// Android substitutes `ModelCatalog.defaultVoice` here
    /// (`VoicePanel.kt:91`) and explains the substitution, because the phone
    /// still has to speak with something. This phone speaks with nothing, so
    /// there is nothing to fall back to, and printing "Kristin" for a house
    /// that speaks with something else would be a false reading rather than
    /// a fallback.
    public static func houseVoiceLabel(_ info: VoiceSettingsInfo) -> String {
        if let listed = info.houseVoice { return label(for: listed) }
        return info.serverVoice
    }

    /// Whether the house's voice is one of the voices this app lists.
    public static func serverVoiceIsListed(_ info: VoiceSettingsInfo) -> Bool {
        info.houseVoice != nil
    }
}
