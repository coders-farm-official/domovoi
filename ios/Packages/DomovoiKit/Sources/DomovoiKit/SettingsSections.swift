import Foundation

/// Settings' groups and sections, as pure data: the five group labels and the
/// sixteen sections with their keys, labels, one-line descriptions, search
/// words, old keys and dashboard addresses.
///
/// A port of `SettingsSection` and `SettingsGroup` in
/// `android/.../ui/screens/settings/SettingsScreen.kt:100-160`, which is what
/// `ANDROID-SHELL-MAP.md` §7.9 means by "port them from that table, not from
/// here". Every `sub` is drawn verbatim, twice — as a section page's second
/// header line and as the group list's row subtitle — so it is a user-facing
/// string and belongs with the rest of §7.
///
/// The screens are later batches'. This is the table, and
/// `ShellStringsTests.testEverySettingsSectionMatchesTheKotlin` reads the
/// Kotlin to check it.
///
/// **Two shapes deliberately left out**, because they are not strings and not
/// this batch's: each section's icon (a Material icon name with no SwiftUI
/// counterpart to choose yet) and the group's icon. Part 2 picks those.
///
/// **Household was "Rooms & devices"** (key `rooms-and-devices`) until the nav
/// regroup (`SettingsScreen.kt:98-105`, commit adb2ad06): it holds Rooms,
/// People, Phones & browsers and Printers & scanners, and App layout moved to
/// System. ``aliases`` keeps the old key, so a link from before still lands
/// (``SettingsGroup/named(_:)``).
public enum SettingsGroup: String, CaseIterable, Sendable {
    case household
    case voice
    case chatAndAi = "chat-and-ai"
    case privacy
    case system

    public var key: String { rawValue }

    /// Keys this group had before, so an old link still opens it.
    public var aliases: [String] {
        switch self {
        case .household: return ["rooms-and-devices"]
        default: return []
        }
    }

    /// The group a key names, by its key or a key it had before
    /// (`groupNamed`, `SettingsScreen.kt:186-188`).
    public static func named(_ key: String?) -> SettingsGroup? {
        guard let key else { return nil }
        return allCases.first { $0.key == key } ?? allCases.first { $0.aliases.contains(key) }
    }

    public var label: String {
        switch self {
        case .household: return "Household"
        case .voice: return "Voice"
        case .chatAndAi: return "Chat & AI"
        case .privacy: return "Privacy"
        case .system: return "System"
        }
    }
}

public struct SettingsSection: Equatable, Sendable {
    /// The route's own key, which a link names.
    public let key: String
    public let label: String
    public let group: SettingsGroup
    /// The line under the label, drawn verbatim as the page header's second
    /// line and as the group list's row subtitle.
    public let sub: String
    /// What a search matches besides the label and the `sub`. Not drawn.
    public let words: String
    /// Keys this section had before, so old links still land.
    public let aliases: [String]
    /// The dashboard's address for a section only the dashboard changes; nil
    /// when the app changes it itself.
    public let dashboard: String?

    public init(
        key: String,
        label: String,
        group: SettingsGroup,
        sub: String,
        words: String = "",
        aliases: [String] = [],
        dashboard: String? = nil
    ) {
        self.key = key
        self.label = label
        self.group = group
        self.sub = sub
        self.words = words
        self.aliases = aliases
        self.dashboard = dashboard
    }
}

public extension SettingsSection {

    /// The sixteen sections, in the Kotlin's declaration order — which is
    /// the order they are listed in, so it is not incidental.
    static let all: [SettingsSection] = [
        SettingsSection(
            key: "rooms", label: "Rooms", group: .household,
            sub: "Each room's device: status, announcements, and each room's details.",
            words: "satellite satellites pi speaker microphone room announce wifi",
            aliases: ["satellites"]
        ),
        SettingsSection(
            key: "people", label: "People", group: .household,
            sub: "Who lives here: each person's profile, what Domovoi remembers, and their conversations.",
            words: "people person family member members household voice profile enrolled memory conversations denylist"
        ),
        SettingsSection(
            key: "phones-and-browsers", label: "Phones & browsers", group: .household,
            sub: "Which server this phone talks to, and who's listening. The household's other phones and browsers are on the dashboard.",
            words: "connection server pair pairing household token phone device identity trust",
            aliases: ["connection"]
        ),
        SettingsSection(
            key: "printers", label: "Printers & scanners", group: .household,
            sub: "The printers and scanners your home server prints and scans with.",
            words: "printer printers print printing scanner scanners scan scanning ink toner paper"
        ),
        SettingsSection(
            key: "phone-calendars", label: "Phone calendars", group: .household,
            sub: "Show this phone's calendars on the household calendar, read-only. Off until you turn it on.",
            words: "calendar calendars share sharing events google microsoft outlook samsung agenda"
        ),
        SettingsSection(
            key: "wake-words", label: "Wake words", group: .voice,
            sub: "The words that wake Domovoi: train your own, record clips in a room.",
            words: "wake word hey hello train",
            dashboard: "wake-words"
        ),
        SettingsSection(
            key: "voices", label: "Voices", group: .voice,
            sub: "How this phone speaks: your Domovoi's voice, downloaded once, or the phone's own.",
            words: "voice speak tts piper pronounce",
            aliases: ["voice"]
        ),
        SettingsSection(
            key: "greetings", label: "Greetings", group: .voice,
            sub: "What a room says when it hears the wake word, before it listens.",
            words: "greeting hello chime",
            dashboard: "greetings"
        ),
        SettingsSection(
            key: "models", label: "Models", group: .chatAndAi,
            sub: "AI chat and questions, and the models behind speech and answers.",
            words: "model ai chat questions llm whisper speech recognition",
            dashboard: "models"
        ),
        SettingsSection(
            key: "chat", label: "Chat", group: .chatAndAi,
            sub: "A heads-up before shaky results, on this phone.",
            words: "chat heads-up heads up warning warn trust shaky calculate exactly reliable"
        ),
        SettingsSection(
            key: "internet", label: "Internet", group: .privacy,
            sub: "Will this Domovoi have internet? It sets the defaults of the online extras.",
            words: "internet online offline network web",
            dashboard: "internet"
        ),
        SettingsSection(
            key: "voice-history", label: "Voice history", group: .privacy,
            sub: "Commands kept from the rooms an admin opted in — reviewed on the dashboard.",
            words: "recordings voice history captures commands kept privacy",
            dashboard: "recordings"
        ),
        SettingsSection(
            key: "about", label: "About & updates", group: .system,
            sub: "What Domovoi is, the user manual, this app's version and updates, and this phone's problem reports.",
            words: "about version update updates manual licences privacy problem reports"
        ),
        SettingsSection(
            key: "health", label: "Health", group: .system,
            sub: "Every check on your Domovoi this phone can see, green or not.",
            words: "health status problems check containers incident server database"
        ),
        SettingsSection(
            key: "app-layout", label: "App layout", group: .system,
            sub: "The tabs along the bottom of this phone, left to right.",
            words: "phone bar tabs bottom bar layout",
            aliases: ["phonebar"]
        ),
        SettingsSection(
            key: "advanced", label: "Advanced", group: .system,
            sub: "The admin account and every setting of the Domovoi server — on the dashboard.",
            words: "advanced configuration config admin password settings",
            aliases: ["server", "configuration"],
            dashboard: "configuration"
        ),
    ]

    /// The section a link names, by its key or by a key it had before.
    /// Kotlin's `SettingsSection.named`.
    static func named(_ key: String?) -> SettingsSection? {
        guard let key else { return nil }
        return all.first { $0.key == key } ?? all.first { $0.aliases.contains(key) }
    }

    /// Sections a shared screen (the kitchen tablet) leaves out: one person's
    /// things, as ``sharedScreenHidden`` leaves People off every launcher
    /// (`SharedScreenHiddenSections`, `SettingsScreen.kt:176-181`).
    /// Presentational, like the rest of the shared view.
    static let sharedScreenHiddenKeys: Set<String> = ["people", "phone-calendars"]

    /// Whether this section is listed on a phone that is (or is not) a shared
    /// screen (`shownOn`).
    func shownOn(sharedScreen: Bool) -> Bool {
        !(sharedScreen && Self.sharedScreenHiddenKeys.contains(key))
    }
}
