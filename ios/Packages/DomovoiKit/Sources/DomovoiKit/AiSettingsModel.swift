import Foundation
import Observation

/// Settings → Chat & AI → Chat: the heads-up switch, and the kinds this phone
/// said "don't warn me about this again" for.
///
/// `ChatSettingsPanel.kt:38-80` is a composable over an `AppContainer`
/// singleton's `StateFlow`. It is a model here for the two reasons every one
/// of these has been a model: the state outlives the rows, and the panel's
/// one real decision is a **choice between three things to draw** rather than
/// a layout.
///
/// ### The state machine, and the bug it is shaped around
///
/// The Kotlin is `if (prefs.off) … else if (prefs.skip.isNotEmpty()) …`
/// (`ChatSettingsPanel.kt:60-63`). Ported as two independent `if`s — the
/// natural SwiftUI transcription — a phone with the heads-up off **and**
/// something silenced draws the off-note *and* the silenced list, which says
/// two contradictory things at once. ``Note`` makes that unrepresentable:
/// there is one note, it is one of three cases, and the view switches over
/// it. A UI case asserts the list is absent while the switch is off; this is
/// what makes that assertion hold by construction rather than by review.
///
/// ### Why the rows do not hold the writes
///
/// Standing rule: a lazy row never holds a `Task`, and writes belong to
/// something that outlives the row. "warn again" removes the row that holds
/// the button, so a row owning its own write is the exact shape that breaks —
/// the write is this model's, and the rows are values it hands out.
@MainActor
@Observable
public final class AiSettingsModel {

    /// The one thing drawn under the switch, which is **one** of three.
    public enum Note: Equatable, Sendable {

        /// The heads-up is off: ``AiSettingsStrings/offNote``, and nothing
        /// else. The silenced list is not drawn here, and that is the whole
        /// point of this enum — see the type's doc comment.
        case off

        /// The heads-up is on and these kinds are silenced, sorted by key
        /// (`prefs.skip.sorted()`, `ChatSettingsPanel.kt:67`): the heading
        /// ``AiSettingsStrings/notWarnedHeading`` and a "warn again" row
        /// each.
        case silenced([Kind])

        /// The heads-up is on and nothing is silenced — no note at all, which
        /// is the state a fresh phone is in.
        ///
        /// Named `nothing` rather than `none`: a case called `none` on a
        /// non-optional enum is the one Swift will quietly read as
        /// `Optional.none` at a comparison, and a test asserting
        /// `note == .none` that compiles against the wrong `none` is a test
        /// that proves nothing.
        case nothing
    }

    /// One silenced kind, as a row reads it: the key the write needs and the
    /// word a person sees.
    ///
    /// `Identifiable` by the key, so a row's identity is the thing the button
    /// writes about — the classic lazy-row capture bug is a button bound to a
    /// row's *position*, and there is no position here.
    public struct Kind: Identifiable, Equatable, Sendable {
        public let key: String
        public let word: String
        public var id: String { key }
    }

    private let book: AiSettingsHeadsUpBook

    /// What this phone has said. Written only through the two methods below,
    /// so `@Observable` sees every change.
    public private(set) var prefs: AiSettingsHeadsUpPrefs

    /// - Parameters:
    ///   - book: where the answer rests. The default is this phone's
    ///     `UserDefaults` blob.
    ///   - arguments: the process arguments, read **only** in a DEBUG build
    ///     and only for ``AiSettingsHeadsUpSeed``'s flag. A UI case needs a
    ///     known switch and a known silenced list, and there is no route and
    ///     no stub-house control that could put one there: the state never
    ///     reaches the house (`DECISIONS.md` §6).
    public init(
        book: AiSettingsHeadsUpBook = AiSettingsHeadsUpBook(),
        arguments: [String] = ProcessInfo.processInfo.arguments
    ) {
        self.book = book
        #if DEBUG
        if let seed = AiSettingsHeadsUpSeed.parse(arguments) { book.replace(seed) }
        #endif
        prefs = book.prefs
    }

    /// Which way the switch reads. **The one place the sign is inverted** —
    /// storage says `off`, the control says `on`
    /// (`ChatSettingsPanel.kt:53`).
    public var isOn: Bool { !prefs.off }

    /// The row's word, which is also its accessibility label: the whole row
    /// is one control for a screen reader on Android
    /// (`ChatSettingsPanel.kt:52-59`, the `Switch` itself takes
    /// `onCheckedChange = null`), so the iPhone's `Toggle` is labelled by the
    /// same word rather than by a second one.
    public var switchWord: String { isOn ? AiSettingsStrings.switchOn : AiSettingsStrings.switchOff }

    /// The one note under the switch. See ``Note``.
    public var note: Note {
        if prefs.off { return .off }
        let keys = prefs.skip.sorted()
        guard !keys.isEmpty else { return .nothing }
        return .silenced(keys.map { Kind(key: $0, word: AiSettingsHeadsUpKinds.word(for: $0)) })
    }

    /// The switch. Takes the control's sense — `true` is warn me — and stores
    /// the Kotlin's.
    public func setOn(_ on: Bool) {
        prefs = book.setOff(!on)
    }

    /// "warn again" on one row (`ChatSettingsPanel.kt:71-73`).
    ///
    /// Takes the key and not the row, because the key is what the store
    /// knows; a ``Kind`` is a thing this model made for the view.
    public func warnAgain(_ key: String) {
        prefs = book.warnAgain(key)
    }

    /// Re-read the place.
    ///
    /// The book reads the place on every access and never caches, so this is
    /// how the panel picks up a write made elsewhere — today, the Chat
    /// screen's own "don't warn me about this again"
    /// (`ChatScreen.kt:1454`), which the ChatMore item is building against
    /// ``AiSettingsHeadsUpBook/neverWarn(_:)``. Called when the section
    /// appears. Android gets it for free from one `StateFlow`; here it is one
    /// `UserDefaults` read, and the alternative is a panel showing a stale
    /// list of kinds a person has just added to.
    public func refresh() {
        prefs = book.prefs
    }
}
