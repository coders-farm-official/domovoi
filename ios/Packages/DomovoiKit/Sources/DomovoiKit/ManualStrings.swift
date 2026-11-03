import Foundation

/// Every sentence the Manual screen says that the document does **not**
/// supply, ported word for word from `ManualScreen.kt`.
///
/// Most of the screen's chrome is in the document itself
/// (``ManualDoc/label(_:_:fallback:)``), which is why this enum is short: a
/// wording that lives in `manual-content.json` is the server's to change, and
/// a copy of it here would be a second source of truth that drifts. What is
/// here is what the Kotlin also hard-codes — the two load failures, which
/// cannot come from a document that did not load, and the handful of joining
/// words — plus the **fallbacks** the Kotlin passes to `label(...)`, so the
/// two platforms word a document with a missing key identically.
///
/// Nothing here is reworded. If a sentence ever is, it goes in
/// `design-notes/ios/overnight/manual/IOS-ONLY-STRINGS.md` with the reason.
public enum ManualStrings {

    // MARK: - the load states

    /// `ManualScreen.kt:338` — hard-coded in the Kotlin too, not a label key.
    public static let cachedCopy =
        "this server can't be reached right now: the manual from last time"

    /// `NoManual` (`ManualScreen.kt:212`).
    public static let noManualTitle = "no manual on this server"
    public static let noManualDetail =
        "this server is older than the manual; its dashboard has one"
    public static let openTheDashboard = "open the dashboard"

    /// The `Failed` branch (`ManualScreen.kt:199`).
    public static let didNotLoadTitle = "the manual didn't load"
    public static let didNotLoadDetail = "this server can't be reached right now"
    public static let tryAgain = "try again"

    // MARK: - joining words

    /// Between a phrase and the reply it gets (`ManualScreen.kt:836`).
    public static let then = "then"

    /// A checklist circle's accessibility label (`ManualScreen.kt:1014`).
    /// The third state — "cannot tell from here" — is the document's
    /// `checklist_unknown`.
    public static let checkDone = "done"
    public static let checkNotYet = "not yet"

    /// A plugin row's pill when the plugin is installed but switched off
    /// (`ManualScreen.kt:962`).
    public static let pluginDisabled = "disabled"

    /// `"${label("next")}: ${title}"` (`ManualScreen.kt:769`).
    public static func next(_ label: String, piece title: String) -> String {
        "\(label): \(title)"
    }

    /// A search result's second line: `"<topic> · <subtitle>"`
    /// (`ManualScreen.kt:414`). Android joins with the topic missing as an
    /// empty string, so a piece whose topic is gone still reads
    /// `" · <subtitle>"` — ported, because inventing a tidier line here would
    /// hide a document that is wrong.
    public static func searchSubtitle(topic: String, subtitle: String) -> String {
        "\(topic) \u{00B7} \(subtitle)"
    }

    /// `listOfNotNull(status, word, nowPlaying).joinToString(" · ")`
    /// (`ManualScreen.kt:961`) — a room's line in the `rooms` slot. The
    /// absent parts are dropped rather than left as gaps.
    public static func roomLine(_ parts: [String?]) -> String {
        parts
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " \u{00B7} ")
    }

    // MARK: - the fallbacks the Kotlin passes to label(...)

    /// Each one is the second argument of a `doc.label(key, fallback = …)`
    /// call in `ManualScreen.kt`, so a document missing the key says on an
    /// iPhone what it says on an Android phone. The shipped file has all of
    /// them; an older server's may not.
    public enum Fallback {
        public static let startHere = "start here"
        public static let map = "map"
        public static let searchPlaceholder = "search the manual"
        public static let searchEmpty = "nothing matches"
        public static let zoomOut = "zoom out"
        public static let eyebrow = "user manual"
        public static let showMap = "show me the map"
        public static let next = "next"
        public static let admin = "admin"
        public static let preview = "preview"
        public static let plugin = "plugin"
        public static let pending = "coming"
        public static let roomsNone = "no rooms yet"
        public static let pluginsNone = "no plugins installed"
        public static let sayNone = "no phrases listed"
        /// `doc.label("checklist_unknown")` is called with **no** fallback
        /// (`ManualScreen.kt:1017`), so a document without the key gives the
        /// circle an empty accessibility label. Kept as the Kotlin has it;
        /// flagged in DECISIONS.md rather than quietly improved.
        public static let checklistUnknown = ""
        /// `map_hint` and `centre_hint` are read with no fallback too
        /// (`ManualScreen.kt:356`).
        public static let mapHint = ""
    }
}
