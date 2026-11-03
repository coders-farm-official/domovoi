import Foundation

/// The People screen's decisions, as functions: who counts as in the room,
/// what a shared name means, what a count says when the read that would have
/// filled it failed, and which colour a name gets.
///
/// Ported from `ui/screens/people/PeopleModels.kt` and
/// `ui/components/Format.kt`. Two of Android's rules are **deliberately
/// absent** — `canRename` and `renameFailureText` — because there is no
/// rename control on this screen (`PATCH /api/people/{id}` is admin-only,
/// see ``PeoplePath``), and a rule with no caller is the `TokenVault`
/// mistake.

// MARK: - who is in the room

/// `isLive(iso, withinSec = 300)` (`Format.kt:77`): a `last_seen_at` within
/// five minutes of now, **in either direction**.
///
/// The absolute difference is Android's (`abs(now - t) < 300`), so a
/// timestamp five minutes in the *future* also reads live. Ported rather than
/// "fixed": the dashboard and the phone have to agree about who is in the
/// room, and a house whose clock is a minute ahead of the phone's would
/// otherwise show a different roster on each.
///
/// **Not added to `Format.swift`**, where Android keeps it: that file is
/// shared DomovoiKit infrastructure, and Satellites and Rooms are being
/// ported tonight in parallel branches that need the same function — two
/// branches adding `isLive` to one file is an `invalid redeclaration` that
/// drops a branch from a train. The consolidation is written down in
/// `overnight/people/DECISIONS.md` under "Needs a shared change".
public func peopleIsLive(_ iso: String?, nowMs: Int64) -> Bool {
    guard let then = isoMs(iso) else { return false }
    return abs(nowMs - then) < peopleLiveWindowMs
}

/// Five minutes, in milliseconds (`Format.kt:77`'s `withinSec = 300`).
public let peopleLiveWindowMs: Int64 = 300_000

// MARK: - one name, one person

/// The longest name the server takes — `PersonRename.name`'s `max_length`
/// and the enrolment limit (`PERSON_NAME_MAX`, `PeopleModels.kt:49`).
///
/// Kept although nothing on this screen renames anybody: it is the limit a
/// **name read off the wire** may legitimately be, and
/// ``cleanPersonName(_:)`` is tested against it.
public let personNameMax = 60

/// A typed or received name as the server keeps it: trimmed, whitespace runs
/// collapsed to one space (`PeopleModels.kt:54`).
///
/// Exactly what the server does with a rename —
/// `" ".join(payload.name.split())` (`people.py:162`) — and `split()` with no
/// argument splits on every kind of whitespace, which is why this splits on
/// `.whitespacesAndNewlines` and not on the space character.
public func cleanPersonName(_ raw: String) -> String {
    raw.components(separatedBy: .whitespacesAndNewlines)
        .filter { !$0.isEmpty }
        .joined(separator: " ")
}

/// The roster subtitle's prefix that tells two people with one name apart:
/// `"#3 · same name · "`, or empty (`PeopleModels.kt:65`).
public func rosterNameTag(_ person: Person) -> String {
    person.namesakes > 0 ? "#\(person.id) · same name · " : ""
}

/// `"#3 · same name · 3d ago · high"` — the whole roster subtitle
/// (`PeopleScreen.kt:289`).
///
/// One function rather than three interpolations in a view, so that the
/// `presence_tier ?: "high"` fallback has one home. Note which way that
/// fallback goes: the roster assumes **`high`** for a person the server sent
/// no tier for, where the profile tile shows an em dash for the same person
/// (`PersonDetail.kt:257`). Both are ported as they are; they disagree on
/// Android too.
public func rosterSubtitle(_ person: Person, nowMs: Int64) -> String {
    "\(rosterNameTag(person))\(relTime(person.lastSeenAt, nowMs: nowMs)) · "
        + (person.presenceTier ?? PeopleStrings.defaultPresenceTier)
}

/// What the profile says about a shared name, or nil when nobody shares it
/// (`PeopleModels.kt:68-75`).
///
/// The first three sentences are Android's, word for word, curly quotes and
/// apostrophes included. **The last one is not**: Android's ends *"If they
/// are different people, rename one of them"*, and there is no rename on this
/// screen, so it names the dashboard instead. That is the one iOS-only
/// sentence this screen adds beyond ``PeopleStrings/renamingIsOnTheDashboard``
/// — `overnight/people/IOS-ONLY-STRINGS.md` carries both and the open
/// question about the wording.
public func namesakeNote(_ person: Person) -> String? {
    guard person.namesakes > 0 else { return nil }
    let name = person.name
    return "\(person.namesakes + 1) people are called \(name). "
        + "When someone says \u{201C}my name is \(name)\u{201D}, "
        + "Domovoi won\u{2019}t guess which one they are. "
        + "If these are the same person, forget the extra one; they can then say "
        + "\u{201C}my name is \(name)\u{201D} wherever Domovoi doesn\u{2019}t know their voice yet. "
        + PeopleStrings.namesakeFixIsOnTheDashboard
}

// MARK: - counts that can say "I don't know"

/// A tab title's count: the number, or `"?"` when the list that would have
/// filled it failed to load (`countLabel`, `PeopleModels.kt:213`).
///
/// F-A008's rule in one function: **a failed read must not read as 0.** The
/// bug it was opened for was a tab saying `memory (0)` for a house whose
/// memories route was 500ing, which is a claim about the person rather than
/// about the request.
public func personCountLabel(_ n: Int, error: String?) -> String {
    error != nil ? "?" : "\(n)"
}

/// One nullable error per sub-list; nil means that list loaded
/// (`PeopleModels.kt:174`).
public struct PersonDetailErrors: Equatable, Sendable {

    public var sessions: String?
    public var conversations: String?
    public var memories: String?
    public var favorites: String?
    public var preferences: String?

    public init(
        sessions: String? = nil,
        conversations: String? = nil,
        memories: String? = nil,
        favorites: String? = nil,
        preferences: String? = nil
    ) {
        self.sessions = sessions
        self.conversations = conversations
        self.memories = memories
        self.favorites = favorites
        self.preferences = preferences
    }

    public static let none = PersonDetailErrors()

    /// The names of the lists that failed, **in tab order** — which is the
    /// order the toast reads them in, so it matches the tabs the reader is
    /// looking at (`PeopleModels.kt:178-185`).
    public var failed: [String] {
        var names: [String] = []
        if sessions != nil { names.append("sessions") }
        if conversations != nil { names.append("conversations") }
        if memories != nil { names.append("memories") }
        if favorites != nil { names.append("favorites") }
        if preferences != nil { names.append("preferences") }
        return names
    }

    public var any: Bool { !failed.isEmpty }

    /// The first failure's own words, in the same order. The *first*, not a
    /// joined list: one toast is one line, and five server messages in it
    /// would be unreadable.
    public var firstMessage: String? {
        [sessions, conversations, memories, favorites, preferences].compactMap { $0 }.first
    }

    /// The memory tab counts memories **and** favourites, so its count is
    /// `"?"` when either failed (`PersonDetail.kt:113-116`).
    public var memoryTab: String? { memories ?? favorites }
}

/// The once-per-load toast when any sub-fetch failed, or nil when all five
/// landed (`personDetailFailureToast`, `PeopleModels.kt:204`).
///
/// `"couldn't load sessions, memories: 500 Server Error: …"` — it names every
/// failed list and quotes the first failure, so that the reader knows it was
/// the server rather than a person with nothing to show.
public func personDetailFailureToast(_ errors: PersonDetailErrors) -> String? {
    guard let first = errors.firstMessage else { return nil }
    return "couldn't load \(errors.failed.joined(separator: ", ")): \(first)"
}

// MARK: - words that count things

/// `"kind"` or `"kinds"` — `plural(n, word)` (`PeopleModels.kt:286`), which
/// returns **the word alone**.
///
/// Prefixed, and not a call to ``plural(_:_:_:)``, because this package's
/// existing `plural` returns `"3 kinds"` — the count included — and People's
/// four call sites all write the count themselves
/// (`"${favsByKind.size} " + plural(favsByKind.size, "kind")`,
/// `PersonTabs.kt:264`). Forwarding would have printed `"2 2 kinds"`. That is
/// the near-miss the existing function's own doc comment warns about, in the
/// other direction, and it is why this one is separate rather than shared.
public func personPlural(_ n: Int, _ word: String) -> String {
    n == 1 ? word : word + "s"
}

// MARK: - the avatar's colour

/// Android's five gradients, as 24-bit RGB pairs (`Kit.kt:140`'s `hues`).
///
/// Hex rather than a `Color`: `DomovoiKit` imports no SwiftUI, and the view
/// turns these into one. **Not what is drawn** — see
/// ``personAvatarGradients``, which darkens these until the initial on top of
/// them is readable. Kept as its own value so the port's starting point is in
/// the source rather than in a commit message, and so the test that proves
/// the darkening changed something has a before to compare against.
public let personAvatarSourceGradients: [(from: UInt32, to: UInt32)] = [
    (0xF2_A6_18, 0xE4_73_18),
    (0x54_BF_5C, 0x2E_8B_57),
    (0x5B_8D_EF, 0x3A_5F_C0),
    (0xB8_6B_D9, 0x8A_4B_AF),
    (0xE4_65_7C, 0xB4_3A_55),
]

/// The five gradients **as drawn**: Android's hues, each end darkened until
/// white text over it clears ``personAvatarInkTarget`` — 5.0 on paper, which
/// is what 4.5:1 on a screen costs.
///
/// ### Why this is not Android's palette
///
/// Android draws a white initial on all five, and **not one** of the five
/// light ends carries it: measured, white over them is 2.05, 2.34, 3.23,
/// 3.42 and 3.26 to one, all under the 4.5:1 a 13-point semibold glyph
/// needs. Apple's audit on the Simulator saw it — `Contrast failed` once and
/// `Contrast nearly passed` twice against People on train S002 — and
/// reported all three *anonymously*, with `element: nil`, because the bubble
/// is `accessibilityHidden`. That is exactly how an unreadable glyph hides
/// from an audit, and why this was answered by measuring every end rather
/// than by widening the suite's classifier.
///
/// The first attempt was Android's palette with a **dark** initial on the two
/// lightest gradients. It passes on the amber and the green and fails on the
/// blue: black at 85% over `#3A5FC0` is 3.27:1, so that one has no ink that
/// works at both ends, and a palette with two rules in it is a palette whose
/// next colour will get the wrong one. Darkening the fill leaves all five
/// with one rule — white, everywhere, as Android has it — and a hue a
/// household would still recognise: `#F2A618` becomes `#95660F`, `#E4657C`
/// becomes `#B24F61`, and the two darkest ends do not move at all.
///
/// Nothing depends on these agreeing with Android's; the bucket already does
/// not (``personAvatarBucket(_:)``). Recorded in
/// `overnight/people/DECISIONS.md`.
public let personAvatarGradients: [(from: UInt32, to: UInt32)] =
    personAvatarSourceGradients.map {
        (
            from: personDarkenedForWhiteInk($0.from, target: personAvatarInkTarget),
            to: personDarkenedForWhiteInk($0.to, target: personAvatarInkTarget)
        )
    }

/// What a fill must clear **on paper** so that it clears 4.5:1 **on screen**.
///
/// Darkening to a bare 4.5 was not enough, and train S008 measured why. Two
/// of the four avatars on the roster came back `Contrast failed`, anonymously
/// as ever — and sampling the screenshot named them: the green's lightest
/// visible fill rendered at **4.46:1** and the amber's at **4.45:1**, against
/// the 4.55 and 4.52 these very numbers say they are. The two crimson
/// avatars, whose circles clip the gradient's light corner away, rendered
/// 4.67 and were not flagged. So the finding tracked the rendered pixel, not
/// the hex: a colour declared in sRGB and composited for a wide-gamut
/// display lands about 2% lighter than its own arithmetic, and a palette
/// sitting a hundredth over the line lands under it.
///
/// 5.0 puts every end at 4.9 or better once drawn — half a stop of headroom
/// for a rendering difference measured at a fiftieth — and leaves the five
/// hues recognisable (`#F2A618` becomes `#95660F`, `#54BF5C` becomes
/// `#377D3C`). It is **not** `personDarkenedForWhiteInk`'s default: 4.5 is
/// what the accessibility guideline says, and this margin is a fact about
/// one screen's drawing, so it belongs at the call site that drew it.
public let personAvatarInkTarget: Double = 5.0

/// A colour's relative luminance, WCAG 2.1's formula over sRGB.
public func personLuminance(_ rgb: UInt32) -> Double {
    func channel(_ shift: UInt32) -> Double {
        let raw = Double((rgb >> shift) & 0xFF) / 255
        return raw <= 0.040_45 ? raw / 12.92 : pow((raw + 0.055) / 1.055, 2.4)
    }
    return 0.2126 * channel(16) + 0.7152 * channel(8) + 0.0722 * channel(0)
}

/// How much contrast white text has over `rgb`.
///
/// White's luminance is 1, so the ratio is `1.05 / (L + 0.05)` — the general
/// form with the lighter side known.
public func personWhiteInkContrast(_ rgb: UInt32) -> Double {
    1.05 / (personLuminance(rgb) + 0.05)
}

/// The same hue, darkened until white over it clears `target`.
///
/// Every channel is scaled by one factor, so the hue and the saturation are
/// the colour's own and only the brightness moves. The search walks the 256
/// factors downwards and returns the **first** that clears the target
/// *after* rounding to whole channels — a binary search over a continuous
/// factor can land a rounded colour a hundredth under the line, which is a
/// contrast finding that would then be very hard to read a reason into.
///
/// A colour that already clears it is returned untouched.
public func personDarkenedForWhiteInk(_ rgb: UInt32, target: Double = 4.5) -> UInt32 {
    for step in stride(from: 255, through: 0, by: -1) {
        let candidate = personScaled(rgb, by: Double(step) / 255)
        if personWhiteInkContrast(candidate) >= target { return candidate }
    }
    return 0x00_00_00
}

/// Every channel of `rgb` multiplied by `factor`, rounded to whole channels.
func personScaled(_ rgb: UInt32, by factor: Double) -> UInt32 {
    func channel(_ shift: UInt32) -> UInt32 {
        let scaled = (Double((rgb >> shift) & 0xFF) * factor).rounded()
        return UInt32(min(255, max(0, scaled)))
    }
    return (channel(16) << 16) | (channel(8) << 8) | channel(0)
}

/// Which of the five a name gets: `0..<5`, stable for ever.
///
/// Android keys this on `abs(name.hashCode()) % 5` (`Kit.kt:149`). Swift's
/// `hashValue` is **seeded per process**, so the same person would be a
/// different colour on every launch — so this writes out its own hash
/// instead: FNV-1a over the name's UTF-8 bytes, which is stable across
/// launches, across devices and across OS versions.
///
/// A given name may therefore land on a different colour than it does on
/// Android. Nothing depends on the two agreeing, and a colour that does not
/// move is the entire point of the feature; the trade is recorded in
/// `overnight/people/DECISIONS.md`.
///
/// The empty name hashes as Android's does, over `"?"` — `AvatarBubble` is
/// given `name ?: "?"` (`Kit.kt:149`), and a nameless row is drawn with the
/// `?` initial.
public func personAvatarBucket(_ name: String) -> Int {
    let bytes = Array((name.isEmpty ? "?" : name).utf8)
    var hash: UInt64 = 0xcbf2_9ce4_8422_2325
    for byte in bytes {
        hash ^= UInt64(byte)
        hash = hash &* 0x100_0000_01b3
    }
    return Int(hash % UInt64(personAvatarGradients.count))
}

/// The letter in the bubble: the first character of the trimmed name, upper
/// cased, or `"?"` (`Kit.kt:141`).
public func personAvatarInitial(_ name: String) -> String {
    guard let first = name.trimmingCharacters(in: .whitespacesAndNewlines).first else { return "?" }
    return String(first).uppercased()
}

// MARK: - the long answer on a turn card

/// Whether a turn's answer is long enough to be worth collapsing
/// (`PersonTabs.kt:678`: `txt.length > 140`).
public func personAnswerIsLong(_ text: String?) -> Bool {
    (text ?? "").count > 140
}

/// The answer as the card shows it: whole, or the first 130 characters with
/// an ellipsis (`PersonTabs.kt:717`).
///
/// Note the two different numbers — long at **141**, cut at **130** — so
/// that opening a barely-long answer reveals more than an ellipsis. Ported
/// as-is; it is not an off-by-one.
///
/// `trimEnd()` is Kotlin's, so a cut that lands on a space does not leave
/// `"… …"`. The ellipsis is one character, U+2026, as the Kotlin's `"…"` is.
public func personAnswerText(_ text: String?, expanded: Bool) -> String {
    let whole = text ?? ""
    guard personAnswerIsLong(whole), !expanded else { return whole }
    let cut = String(whole.prefix(130))
    return cut.replacingOccurrences(
        of: "\\s+$",
        with: "",
        options: .regularExpression
    ) + "\u{2026}"
}

// MARK: - the roster's four states

/// What the roster shows: the rows, or which of three other things
/// (`PeopleScreen.kt:200-224`).
///
/// A value rather than four `if`s in the view, because the two empty notes
/// are **not** interchangeable — "nobody enrolled yet" about a house whose
/// filter matched nothing is a lie about the house — and a view cannot be
/// asked whether it got that right. The order is Android's: loading and
/// failed both only while there is nothing to show at all, so a refresh that
/// fails leaves the rows on screen.
public enum PeopleRosterState: Equatable, Sendable {
    case loading
    case failed(String)
    /// The house has nobody enrolled.
    case nobodyEnrolled
    /// Somebody is enrolled, and the filter matched none of them.
    case noMatch
    case rows([Person])
}
