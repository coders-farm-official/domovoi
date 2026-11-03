import Foundation
import Synchronization

/// This phone's answers to the heads-up before a shaky result: the master
/// switch, and the kinds of task it said "don't warn me about this again" for.
///
/// A port of `HeadsUpPrefs` and `HeadsUpStore`
/// (`android/.../assistant/docs/HeadsUpStore.kt:11`, `:20-47`), read on
/// 2026-11-02 for Settings → Chat & AI → Chat.
///
/// ### This is phone-local state, and there is no route for it
///
/// Nothing here is a server call, and the Chat panel never posts anything.
/// Android's Chat screen sends `{off, skip[]}` with **every message** under
/// the key `heads_up` (`ChatDocsApi.kt:105-107`, put into the send body at
/// `ChatScreen.kt:327` and `:334`) and the server's own rules decide whether
/// a card shows — the phone only says what it silenced. So the switch is a
/// write to this phone, not an admin change, which is why it is the one
/// Chat & AI section an app without an admin session can actually draw
/// (`SettingsModels.kt:30-36` is the rule the other two obey).
public struct AiSettingsHeadsUpPrefs: Equatable, Sendable {

    /// The master switch, stored the way Android stores it — **true means
    /// silenced**. The sign is inverted once, at the switch
    /// (`ChatSettingsPanel.kt:53`, `checked = !prefs.off`), and
    /// ``AiSettingsModel`` is the only place that does the inverting.
    public var off: Bool

    /// The kinds silenced one at a time, by key. Filtered against
    /// ``AiSettingsHeadsUpKinds/kinds`` on the way out of storage
    /// (`HeadsUpStore.kt:29`).
    public var skip: Set<String>

    public init(off: Bool = false, skip: Set<String> = []) {
        self.off = off
        self.skip = skip
    }
}

/// The eight kinds of task that can be silenced, and what each one is called
/// where a person reads it.
public enum AiSettingsHeadsUpKinds {

    /// `DocHeadsUp.KINDS` = `DOC_KINDS + PICTURE_KINDS`
    /// (`android/.../assistant/docs/DocHeadsUp.kt:42-44`), in that order.
    /// The order is not drawn anywhere — a list of silenced kinds is sorted
    /// by key (`ChatSettingsPanel.kt:67`) — it is here so that the set a
    /// stored blob is filtered against is the Kotlin's set, exactly.
    public static let kinds: [String] = [
        "scanned", "language", "calc", "compare", "rewrite", "long",
        "blurry", "handwriting",
    ]

    /// `HeadsUpStore.KIND_WORDS` (`HeadsUpStore.kt:52-57`), word for word.
    public static let words: [String: String] = [
        "rewrite": "rewriting a whole document",
        "compare": "comparing parts of a long document",
        "calc": "exact sums, averages and dates",
        "long": "long summaries that take a while",
        "scanned": "scanned pages",
        "language": "languages not tested yet",
        "blurry": "blurry photos",
        "handwriting": "handwriting",
    ]

    /// The word for a kind, falling through to the **raw key** for one this
    /// build has no word for — `KIND_WORDS[kind] ?: kind`
    /// (`ChatSettingsPanel.kt:69`).
    ///
    /// Reached only for a key a newer server silenced through a route this
    /// build does not know: ``AiSettingsHeadsUpBook`` filters an unknown key
    /// out of storage, so a stored one never gets here. Kept anyway, because
    /// Android keeps it and because listing such a kind by its key is better
    /// than dropping a row a person cannot then clear.
    public static func word(for kind: String) -> String {
        words[kind] ?? kind
    }
}

/// Where ``AiSettingsHeadsUpPrefs`` rests between launches.
///
/// Android's is a `SharedPreferences` file of its own (`heads_up`,
/// `HeadsUpStore.kt:46-47`) behind an `AppContainer` singleton with a
/// `StateFlow`, so Settings and the Chat screen see each other's writes live.
///
/// ### Why this reads the place on every access instead of caching it
///
/// ``ChatPinBook`` — the same seam, the same shape — reads once in `init` and
/// answers from memory, which is right for pins because one model owns them.
/// This state has **two** readers: the Settings panel writes it and a live
/// `ChatModel` sends it with the next message. `AppContainer` is on this
/// night's do-not-edit list, so there is no singleton to hang a shared
/// instance off; two caching instances would diverge silently, and the stale
/// one would be the one talking to the server. One `UserDefaults` read per
/// access is cheaper than that bug, and a switch row can afford it.
///
/// `Sendable` without `@unchecked`, unlike ``ChatPinBook``: there is no
/// stored mutable state here to be unchecked about — the place **is** the
/// state. The lock below is not protecting a property; see its own comment.
public final class AiSettingsHeadsUpBook: Sendable {

    /// The saved shape: one blob, one key.
    ///
    /// **Every field is optional on the way in**, for the reason
    /// ``ChatPinBook``'s `Saved` and ``TrustBook/init(from:)`` give at
    /// length: a blob written by a build that did not have a field must not
    /// throw and lose the person's answer.
    struct Saved: Codable, Sendable {
        var off: Bool = false
        var skip: Set<String> = []

        enum CodingKeys: String, CodingKey { case off, skip }

        init(off: Bool = false, skip: Set<String> = []) {
            self.off = off
            self.skip = skip
        }

        init(from decoder: any Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            off = try values.decodeIfPresent(Bool.self, forKey: .off) ?? false
            skip = try values.decodeIfPresent(Set<String>.self, forKey: .skip) ?? []
        }
    }

    /// Not a property lock — there is no property. It serialises the
    /// read-modify-write of the three writers below, which is the one thing
    /// that is not atomic in a store keeping nothing in memory: two
    /// `warnAgain`s racing could otherwise both read the same set, and the
    /// second write would put the first kind back.
    private let gate = Mutex(0)

    private let place: any SavedState

    public init(
        place: any SavedState = UserDefaultsSavedState(key: UserDefaultsSavedState.aiSettingsHeadsUpKey)
    ) {
        self.place = place
    }

    /// What this phone has said, read from the place now.
    ///
    /// A blob this build cannot read at all answers as the default — the
    /// heads-up on, nothing silenced — rather than throwing: the worst case
    /// is a person warned about something they had silenced, and the
    /// alternative is a Settings section that crashes on a phone whose owner
    /// cannot clear it.
    ///
    /// `skip` is filtered against ``AiSettingsHeadsUpKinds/kinds`` on the way
    /// out, as `HeadsUpStore.read()` does (`HeadsUpStore.kt:29`): a key
    /// written by another build is dropped rather than listed.
    public var prefs: AiSettingsHeadsUpPrefs {
        guard let blob = place.read(),
              let saved = try? JSONDecoder().decode(Saved.self, from: blob)
        else { return AiSettingsHeadsUpPrefs() }
        return AiSettingsHeadsUpPrefs(
            off: saved.off,
            skip: saved.skip.filter(AiSettingsHeadsUpKinds.kinds.contains)
        )
    }

    /// `HeadsUpStore.setOff` (`HeadsUpStore.kt:38`). Returns what is now
    /// stored, so a caller never has to read back what it just wrote.
    @discardableResult
    public func setOff(_ off: Bool) -> AiSettingsHeadsUpPrefs {
        change { $0.off = off }
    }

    /// `HeadsUpStore.neverWarn` (`HeadsUpStore.kt:39`) — "don't warn me about
    /// this again", which on Android is the **Chat screen's** card button
    /// (`ChatScreen.kt:1454`) and not this panel's.
    ///
    /// It has no caller in this item on purpose, and that is written down
    /// rather than left to be rediscovered as an unread seam
    /// ([[reasoning-that-hides-a-bug]]'s `TokenVault`): the ChatMore item is
    /// handed this book and this method in
    /// `design-notes/ios/overnight/aisettings/DECISIONS.md` §4, because a
    /// second store over a second key would split one person's answer in two.
    @discardableResult
    public func neverWarn(_ kind: String) -> AiSettingsHeadsUpPrefs {
        change { $0.skip.insert(kind) }
    }

    /// `HeadsUpStore.warnAgain` (`HeadsUpStore.kt:40`) — the panel's
    /// "warn again" button (`ChatSettingsPanel.kt:71`).
    @discardableResult
    public func warnAgain(_ kind: String) -> AiSettingsHeadsUpPrefs {
        change { $0.skip.remove(kind) }
    }

    /// The whole answer at once. Internal, with its two callers named:
    /// ``AiSettingsModel``'s DEBUG launch seed (``AiSettingsHeadsUpSeed``)
    /// and the package tests. No view writes this, which is why it is not
    /// public.
    func replace(_ prefs: AiSettingsHeadsUpPrefs) {
        gate.withLock { _ in save(prefs) }
    }

    private func change(
        _ edit: (inout AiSettingsHeadsUpPrefs) -> Void
    ) -> AiSettingsHeadsUpPrefs {
        gate.withLock { _ in
            var next = prefs
            edit(&next)
            save(next)
            return next
        }
    }

    /// An encode failure is a programming mistake — a `Bool` and a
    /// `Set<String>` — so, as ``SavedShellStore`` does with the shell's blob,
    /// it is survived rather than crashed on: nothing is written, and the
    /// next read answers what was there before.
    private func save(_ prefs: AiSettingsHeadsUpPrefs) {
        guard let blob = try? JSONEncoder().encode(Saved(off: prefs.off, skip: prefs.skip)) else {
            assertionFailure("the heads-up answer would not encode")
            return
        }
        place.write(blob)
    }
}

public extension UserDefaultsSavedState {

    /// The heads-up answer's key.
    ///
    /// A key of its own under the same prefix, for the reason
    /// ``UserDefaultsSavedState/chatPinsKey`` gives: the shell's blob is one
    /// write of the server, the trust and the tokens, and a switch in
    /// Settings has nothing to do with any of them.
    ///
    /// Declared in this item's own file rather than beside the other two,
    /// because `SavedShellStore.swift` is shared and twenty branches editing
    /// one file collide in a train.
    static let aiSettingsHeadsUpKey = "domovoi.aisettings.headsUp"
}

/// The DEBUG launch argument a UI case seeds this phone's answer with:
/// `-domovoi-aisettings-headsup off:calc,scanned`.
///
/// Android's instrumentation does not need one — it builds
/// `HeadsUpStore.memory()` (`HeadsUpStore.kt:49`) and hands it to the
/// composable. A UI case here drives the shipping app, and `UserDefaults`
/// survives between cases in one run, so without a seed the second case of a
/// run starts from whatever the first left behind. It is also the only way to
/// reach a non-empty silenced list before the ChatMore item ships the control
/// that fills one. `DECISIONS.md` §6 carries the call.
///
/// Parsed here, in the package, so the grammar is tested without a Simulator;
/// applied in ``AiSettingsModel`` under `#if DEBUG` only, so no shipping
/// build reads it.
public enum AiSettingsHeadsUpSeed {

    public static let flag = "-domovoi-aisettings-headsup"

    /// `nil` when the flag is absent or its value is not one of the two
    /// words — **not** a default: a typo must leave the phone's real answer
    /// alone rather than quietly reset it, and a case that mistyped the flag
    /// should then fail on the state it asserts.
    ///
    /// Unknown kinds are dropped here as well as on read, so a seed cannot
    /// put a row on screen that the store would refuse to keep.
    public static func parse(_ arguments: [String]) -> AiSettingsHeadsUpPrefs? {
        guard let index = arguments.firstIndex(of: flag) else { return nil }
        let next = arguments.index(after: index)
        guard next < arguments.endIndex else { return nil }
        let halves = arguments[next].split(
            separator: ":", maxSplits: 1, omittingEmptySubsequences: false
        )
        let off: Bool
        switch String(halves.first ?? "") {
        case "off": off = true
        case "on": off = false
        default: return nil
        }
        let kinds = halves.count > 1
            ? halves[1].split(separator: ",").map(String.init)
                .filter(AiSettingsHeadsUpKinds.kinds.contains)
            : []
        return AiSettingsHeadsUpPrefs(off: off, skip: Set(kinds))
    }
}
