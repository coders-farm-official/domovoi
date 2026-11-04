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

    /// A plugin row's version number, **spoken**.
    ///
    /// iOS-only, and the one sentence in this enum Android has no line for:
    /// `ManualScreen.kt:956` draws the version as bare text and Compose's
    /// audit has no counterpart to Apple's "Label not human-readable", which
    /// train S009 reported against `0.4.1`. VoiceOver reading "zero point
    /// four point one" beside a plugin's name says nothing about what the
    /// number is; the screen still *shows* the bare version, as Android does.
    /// Recorded in `design-notes/ios/overnight/manual/IOS-ONLY-STRINGS.md`.
    public static func pluginVersion(_ version: String) -> String {
        "version \(version)"
    }

    // MARK: - what the swipe back does, said out loud

    /// `zoom out`'s VoiceOver hint below the accessibility sizes: the gesture
    /// does the same thing this control does.
    ///
    /// iOS-only, and there is no Android line for it — Android's way up is a
    /// hardware or gestural **back**, which the platform announces itself,
    /// while iOS's leading-edge swipe is announced by nobody. Kamron's answer
    /// 32 put the gesture on this screen; a gesture a VoiceOver user cannot
    /// discover is a control that may as well not exist, so the control that
    /// does the same job says so. Recorded with the other iOS-only sentences
    /// in this item's notes.
    public static let swipeGoesUpHint = "a swipe back from the left edge does this too"

    /// The same hint at an accessibility text size, where the swipe does
    /// something else.
    ///
    /// There the top bar and the page share one scroll view and the levels
    /// are swapped in place rather than pushed (``ManualView/ready(_:cached:_:)``),
    /// so the gesture pops the **route** and leaves the manual. Saying "a
    /// swipe back does this too" there would be the app telling a VoiceOver
    /// user something untrue of the screen in front of them, which is
    /// Kamron's no-misleading-controls rule pointed at a hint; entry 74 in
    /// `DECISIONS-FOR-KAMRON.md` is his open question about the divergence
    /// itself.
    public static let swipeLeavesTheManualHint =
        "at this text size a swipe back leaves the manual; this goes up a level"

    /// Which of the two `zoom out` says, asked **here** and not in a view
    /// body.
    ///
    /// ``ShellHeaderFold``'s reason, which this app has already paid for: a
    /// review's negative control broke two size gates written as ternaries
    /// inside view bodies and nothing in the repository noticed, because
    /// `pkgtest` builds the package and not the views. A sentence that is
    /// wrong at one text size is the no-misleading-controls rule broken by a
    /// hint, so the choice is a function with a test on it
    /// (`ManualAnswersTests`).
    public static func swipeHint(isAccessibilitySize: Bool) -> String {
        isAccessibilitySize ? swipeLeavesTheManualHint : swipeGoesUpHint
    }

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
        /// A checklist circle the app cannot judge, on a server whose document
        /// predates the key — **Kamron's answer 33**, "can't tell from here on
        /// both phones".
        ///
        /// These are the words the generator already writes:
        /// `scripts/manual/build_manual.py:361` puts
        /// `"checklist_unknown": "can't tell from here"` in `DEFAULT_LABELS`
        /// and `web/static/manual-content.json:30` carries it, so every server
        /// built from this repository supplies them and this fallback is only
        /// ever read on an older one. It was `""` — ported from
        /// `doc.label("checklist_unknown")`, which Android calls with no
        /// fallback (`ManualScreen.kt:1016`) — and an empty string is an
        /// unlabelled control: the circle is drawn, VoiceOver reads it as an
        /// image with no name, and the third state ("neither ticked nor
        /// crossed") is the one state that *needs* words, because the shape
        /// alone cannot say "this app cannot tell".
        ///
        /// The read still goes through this named fallback
        /// (`ManualPageView.swift`'s `label(of:)`), not an inline literal, so
        /// the wording stays in one place. Android's half of answer 33 is the
        /// android unit's.
        public static let checklistUnknown = "can't tell from here"
        /// `map_hint` and `centre_hint` are read with no fallback too
        /// (`ManualScreen.kt:356`).
        public static let mapHint = ""
    }
}
