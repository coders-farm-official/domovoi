import Foundation

/// Every word the Voices section says, and the three signposts' one extra
/// line — the shape ``ChatStrings`` and ``ShellStrings`` have, each string
/// with the file and line it came from.
///
/// Most of this section's wording is **iOS-only**, and the reason is in the
/// map's first paragraph: Android's `VoiceCard` (`VoicePanel.kt:117-259`) is
/// about a Piper voice downloaded onto the **phone**, so the phone can speak
/// on its own. This phone speaks with nothing — no phonemizer
/// (`ios/Packages/DomovoiPhonemizer/` is empty on purpose), no ONNX Runtime,
/// no model store — so the section's subject is different, not smaller: it
/// reads what the **server** says about its voice and plays a sample the
/// server renders. A sentence about downloading a voice to this phone would
/// be false, and Kamron's rule is that a control (or a sentence) that cannot
/// do what it says is not drawn. Every changed sentence is listed, with its
/// reason, in `design-notes/ios/overnight/voicesettings/IOS-ONLY-STRINGS.md`.
///
/// The ported-word-for-word ones are marked **ported**; everything else is
/// marked **iOS-only** here as well as in that file.
///
/// ## What is not here, and why there is no control for it
///
/// No wording for the voice **picker** (`VoicePanel.kt:161`), the
/// **download** or **pause download** buttons (`:204-207`), **delete** in
/// either form (`:213`, `:236-242`) or its confirm dialog (`:250-259`),
/// **allow mobile data** (`:217-228`), the **ready / NN% / not downloaded**
/// pill (`:172-179`), the download-size line (`:181`), the
/// "Until it is downloaded…" note (`:186-197`), the **Also on this phone**
/// list (`:229-243`), or the built-in **"say it like" preview**
/// (`voice.py:278`). Each of those names a thing this phone cannot do, so
/// there is no control, and so there is no string.
public enum VoiceSettingsStrings {

    // MARK: - the page

    /// **ported** — `PanelCard("Voice", …)`, `VoicePanel.kt:155`. The
    /// section's own label is "Voices" (`SettingsSections.swift`); this is
    /// the card's title, as it is over there.
    public static let voiceTitle = "Voice"

    /// **iOS-only.** Android's sub (`VoicePanel.kt:156-158`) promises "the
    /// same voice as your Domovoi, downloaded to the phone once" and names
    /// the chat mic's replies — three things this app does not have. Its last
    /// clause, about room mode playing its room's voice, is kept because it
    /// is true here too.
    public static let voiceSub = "The voice your Domovoi speaks with, and how it says names. "
        + "Room mode is different: it plays its room's voice, which the server renders "
        + "(say \"switch to Kristin's voice\" in the room to change it)."

    // MARK: - the house's voice

    /// **iOS-only.**
    public static let houseVoiceTitle = "The house's voice"

    /// **iOS-only.** The honest half of `voiceSourceNote`'s last branch
    /// (`VoicePanel.kt:95`, "The voice your Domovoi speaks with, on the phone
    /// and in the chat mic's replies") with the two phone clauses removed.
    public static let houseVoiceSub = "What every room speaks with, unless a room has a voice of its own."

    /// **iOS-only.** The house speaks with a voice this app does not list —
    /// reachable whenever the registry's default is not a shipped voice
    /// (`voice_settings.py:143-152`). Android substitutes its own default
    /// here and explains the substitution (`VoicePanel.kt:91`); this names
    /// what the server actually said, because nothing on this phone has to
    /// speak.
    public static func notOneOfTheThree(_ id: String) -> String {
        "The server speaks with \(id), which is not one of the three below."
    }

    /// **iOS-only.** `updated_at`, read with ``parseInstant(_:)`` and
    /// rendered by the view; this is the label in front of it.
    public static let lastChanged = "last changed"

    // MARK: - the three voices

    /// **iOS-only.**
    public static let voicesTitle = "The voices"

    /// **iOS-only.**
    public static let voicesSub = "The three voices your Domovoi can speak with. An admin picks which, on the dashboard."

    /// **ported** — the tail of the facts line (`VoicePanel.kt:181`,
    /// `"… · Piper, 22 kHz"`), driven by the server's own `sample_rate`
    /// rather than by a literal. The size and the licence are left out: see
    /// DECISIONS, "The download size is not printed".
    public static func engineAndRate(sampleRate: Int) -> String {
        guard sampleRate > 0 else { return "Piper" }
        return "Piper, \(Int((Double(sampleRate) / 1000).rounded())) kHz"
    }

    /// **ported** — `VoicePanel.kt:210`.
    public static let playASample = "play a sample"

    /// **ported** — `VoicePanel.kt:210`, the same button while it plays.
    public static let stop = "stop"

    /// **iOS-only.** Why a voice has no play button. Drawn as a quiet line on
    /// the row, not as a disabled button: a disabled control still says the
    /// app could do the thing.
    public static let noSampleToPlay = "No sample this server can speak yet."

    /// **iOS-only.** The sample was asked for and did not arrive. The
    /// server's own words are passed through, as every other failure line on
    /// this track does.
    public static func sampleFailed(_ reason: String) -> String {
        "The sample could not be played: \(reason)"
    }

    // MARK: - how names are said

    /// **iOS-only.**
    public static let namesTitle = "How names are said"

    /// **ported** — `pronunciationNote`'s server branch
    /// (`VoicePanel.kt:103-106`), including its singular/plural. It is the
    /// only one of that function's three branches with a subject here: the
    /// list this section draws **is** the server's effective list, so there
    /// is no "the built-in one is in force" and no converter to refuse an
    /// entry.
    public static func namesSaidByTheServer(count: Int) -> String {
        "Names are said the way the server's \"say it like\" list says "
            + "(\(count) \(count == 1 ? "entry" : "entries"))."
    }

    /// **iOS-only.** An empty list is a real answer — the owner emptied it —
    /// and must not read as a read that failed. Android cannot reach this
    /// wording: an empty list there falls into its `else` branch and claims
    /// the built-in list is in force, which is true of the phone's own
    /// converter and not of the server.
    public static let noNamesList = "Nothing is respelled: every name is said the way it is spelled."

    /// **iOS-only.** One row of the list: `Nguyen → win`. The arrow is
    /// U+2192.
    public static func saidAs(word: String, like: String) -> String {
        "\(word) → \(like)"
    }

    // MARK: - what this phone does not do

    /// **iOS-only.** The whole of this card is iOS-only, and it exists so the
    /// section is not *silently* thinner than Android's: a person comparing
    /// the two should be told, not left to wonder.
    public static let notOnThisPhoneTitle = "Not on this phone yet"

    /// **iOS-only.**
    public static let notOnThisPhoneBody = "This app does not speak. On Android, the house's voice can be "
        + "downloaded to the phone so it can read replies out loud without the server; the iPhone app has none "
        + "of that yet, so there is nothing here to pick, download or delete. What it can do is play a sample "
        + "the server speaks."

    // MARK: - the read

    /// **iOS-only.** `GET /api/voice` did not answer. Said rather than drawn
    /// as an empty card — the whole point of ``ApiRead``'s `answered`.
    public static func voiceReadFailed(_ reason: String) -> String {
        "Could not read the voice settings: \(reason)"
    }

    /// **iOS-only.** While the first read is in flight.
    public static let checking = "checking…"

    // MARK: - the signposts

    /// **iOS-only**, and only a VoiceOver label: the monospaced address the
    /// signpost prints is the string itself, which read aloud as a bare URL
    /// says nothing about what it is.
    public static func dashboardAddress(_ url: String) -> String {
        "the dashboard, at \(url)"
    }
}
