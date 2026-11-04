import Foundation

/// Every decision the manual makes, as free functions over the document —
/// the pure half of `ManualContent.kt` (`:176` onwards), which the JVM tests
/// (`ManualContentTest`) cover there and the package tests cover here.
///
/// They are functions rather than methods on ``ManualDoc`` for the reason
/// Android's are top-level: the screen is the only caller, and a document
/// read off a server should not have behaviour attached to it. The one that
/// needed a signature change is ``manualRouteOpens(_:_:)`` — see its note.
///
/// **No regular expression in this file uses `\d`, `\s` or `\w`.** Kotlin's
/// `Regex` is `java.util.regex`, whose classes are ASCII; `NSRegularExpression`
/// is ICU, whose `\d` matches Arabic-Indic digits and whose `\s` matches the
/// Unicode spaces. A version label of `١.٠.٠` would then have passed
/// ``manualDocRef(_:)`` and sent a "More detail" link to a tag that does not
/// exist. Each one is written out over the characters instead, which is what
/// ``ServerAddress`` and ``DeviceRegistry`` already do and for the same
/// reason.

// MARK: - what this server shows

/// The phrases of a `say` block this server answers (`ManualContent.kt:176`).
///
/// A phrase whose ``ManualPhrase/needs`` feature is reported **false** is
/// left out, and so is the `then` reply that follows it; a feature the server
/// says nothing about shows the phrase. So an older server, and a manifest
/// that failed to load, both keep every phrase — the same way
/// ``Capabilities/aiEnabled`` treats an absent `features.ai`.
public func shownManualPhrases(
    _ phrases: [ManualPhrase],
    features: [String: Bool]
) -> [ManualPhrase] {
    phrases.filter { phrase in
        guard let needs = phrase.needs else { return true }
        return features[needs] != false
    }
}

/// Shown in the app: everything but the dashboard-only lines
/// (`ManualContent.kt:180`). Seven blocks in the shipped file are `app`-only
/// and one is `dashboard`-only.
public func shownInApp(_ only: String?) -> Bool {
    only == nil || only == "app"
}

// MARK: - the wake word

/// A room as the manual needs it — `GET /api/satellites`
/// (`ManualContent.kt:229`; the route is `web/backend/api/satellites.py:136`,
/// `response_model=list[Satellite]`, so the body is a **bare array**).
public struct ManualRoom: Equatable, Sendable, Decodable {

    public let roomID: String
    public let roomLabel: String?
    /// `online` | `offline` | `waiting`.
    public let status: String
    /// The wake-word slug the room's detector runs (`hey_jarvis`).
    public let wakeWord: String?
    /// What the registry calls that word, where the room carries its own.
    public let wakePhrase: String?
    public let lastSeenAt: String?

    /// **The shape Android gets wrong.** `ManualNowPlaying`
    /// (`ManualContent.kt:239`) decodes `now_playing.title` and
    /// `now_playing.file`, but the server carries the track one level down,
    /// under `song` (`web/backend/schemas.py:236`) — so Android's
    /// `r.nowPlaying?.title ?: r.nowPlaying?.file`
    /// (`ManualScreen.kt:959`) is always null and the manual's rooms slot has
    /// never been able to say what is playing. This reads the real shape, and
    /// the Android defect is reported rather than copied.
    ///
    /// ``HomeNowPlaying`` is that shape already, decoded in this module for
    /// Home's rooms section; a second decoder for the same object would be
    /// the next bug of this kind.
    public let nowPlaying: HomeNowPlaying?

    public init(
        roomID: String = "",
        roomLabel: String? = nil,
        status: String = "",
        wakeWord: String? = nil,
        wakePhrase: String? = nil,
        lastSeenAt: String? = nil,
        nowPlaying: HomeNowPlaying? = nil
    ) {
        self.roomID = roomID
        self.roomLabel = roomLabel
        self.status = status
        self.wakeWord = wakeWord
        self.wakePhrase = wakePhrase
        self.lastSeenAt = lastSeenAt
        self.nowPlaying = nowPlaying
    }

    enum CodingKeys: String, CodingKey {
        case status
        case roomID = "room_id"
        case roomLabel = "room_label"
        case wakeWord = "wake_word"
        case wakePhrase = "wake_phrase"
        case lastSeenAt = "last_seen_at"
        case nowPlaying = "now_playing"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        roomID = try c.decodeIfPresent(String.self, forKey: .roomID) ?? ""
        roomLabel = try c.decodeIfPresent(String.self, forKey: .roomLabel)
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? ""
        wakeWord = try c.decodeIfPresent(String.self, forKey: .wakeWord)
        wakePhrase = try c.decodeIfPresent(String.self, forKey: .wakePhrase)
        lastSeenAt = try c.decodeIfPresent(String.self, forKey: .lastSeenAt)
        nowPlaying = try c.decodeIfPresent(HomeNowPlaying.self, forKey: .nowPlaying)
    }

    /// `ManualRoom.name` (`ManualContent.kt:237`): the label, or the id.
    public var name: String {
        if let label = roomLabel, !label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return label
        }
        return roomID
    }

    /// What the room is playing, as the slot means it: the song's title, else
    /// its file. Nil when nothing is loaded.
    public var song: String? {
        guard let song = nowPlaying?.song else { return nil }
        return song.title ?? song.file
    }
}

/// `titleWords` (`ManualContent.kt:241`): `living_room` → `Living Room`.
public func manualTitleWords(_ text: String?) -> String {
    let spaced = (text ?? "").map { $0 == "_" || $0 == "-" ? " " : $0 }
    // Kotlin replaces the `[_-]+` run with one space, trims and splits on
    // `\s+`; splitting on each space and dropping the empties is the same
    // thing without a regex engine.
    return manualWords(String(spaced))
        .map { word in
            // Kotlin's `replaceFirstChar { it.uppercaseChar() }` — the first
            // character only, and never a two-character uppercasing.
            word.prefix(1).uppercased() + word.dropFirst()
        }
        .joined(separator: " ")
}

/// The words a room answers to, as people say them (`ManualContent.kt:246`):
/// its registry phrase title-cased, else the document's name for the slug,
/// else the slug title-cased. Nil when the room runs no detector.
public func manualWakePhrase(_ wake: ManualWakeWord, _ room: ManualRoom) -> String? {
    guard let slug = room.wakeWord else { return nil }
    if let phrase = room.wakePhrase,
       !phrase.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        return manualTitleWords(phrase)
    }
    return wake.names[slug] ?? manualTitleWords(slug)
}

/// One of the words that is **not** the one being named, with the rooms that
/// answer to it. Kotlin's `Pair<String, List<String>>` (`ManualContent.kt:254`).
public struct ManualWakeOther: Equatable, Sendable {
    public let phrase: String
    public let rooms: [String]

    public init(phrase: String, rooms: [String]) {
        self.phrase = phrase
        self.rooms = rooms
    }
}

/// `WakeInfo` (`ManualContent.kt:254`).
public struct ManualWakeInfo: Equatable, Sendable {
    /// The word the manual names in `{wake}`.
    public let lead: String
    public let others: [ManualWakeOther]

    public init(lead: String, others: [ManualWakeOther] = []) {
        self.lead = lead
        self.others = others
    }
}

/// `wakeInfo` (`ManualContent.kt:256`): which word to name, and which others
/// to mention.
///
/// The out-of-the-box word while **any** room answers to it — or while
/// nothing is known yet, which is the case the whole screen renders under for
/// its first moment. Only when every known room has moved off the default
/// does the manual name the word most rooms use.
public func manualWakeInfo(_ wake: ManualWakeWord, rooms: [ManualRoom]?) -> ManualWakeInfo {
    let known = (rooms ?? []).filter { $0.wakeWord != nil }
    // Insertion-ordered, like Kotlin's `LinkedHashMap`: the order decides
    // nothing on its own (the comparator below is total) but it keeps the
    // grouping readable in a failure message.
    var order: [String] = []
    var groups: [String: [String]] = [:]
    for room in known {
        guard let phrase = manualWakePhrase(wake, room) else { continue }
        if groups[phrase] == nil { order.append(phrase) }
        groups[phrase, default: []].append(room.name)
    }
    let fallback = wake.names[wake.default] ?? manualTitleWords(wake.default)

    /// `compareByDescending { size }.thenBy { key }` — most rooms first, then
    /// alphabetically. Total, because the phrases are a set's worth of keys.
    func before(_ lhs: String, _ rhs: String) -> Bool {
        let (left, right) = (groups[lhs]?.count ?? 0, groups[rhs]?.count ?? 0)
        return left == right ? lhs < rhs : left > right
    }

    let lead: String
    if !known.isEmpty, !known.contains(where: { $0.wakeWord == wake.default }) {
        lead = order.sorted(by: before).first ?? fallback
    } else {
        lead = fallback
    }
    let others = order
        .filter { $0 != lead }
        .sorted(by: before)
        .map { ManualWakeOther(phrase: $0, rooms: groups[$0] ?? []) }
    return ManualWakeInfo(lead: lead, others: others)
}

/// `fill` (`ManualContent.kt:269`): `{name}` from `vars`, anything else left
/// as written.
///
/// Written out rather than as `Regex("\\{(\\w+)\\}")`: ICU's `\w` takes every
/// Unicode word character, so `{Zimmer}` and `{комната}` would be substituted
/// on iOS and left alone on Android. A name here is ASCII word characters,
/// which is what Java's `\w` is.
public func manualFill(_ template: String, _ vars: [String: String]) -> String {
    var out = ""
    var rest = Substring(template)
    while let open = rest.firstIndex(of: "{") {
        out += rest[rest.startIndex..<open]
        let afterOpen = rest.index(after: open)
        let name = rest[afterOpen...].prefix { character in
            character.isASCII && (character.isLetter || character.isNumber || character == "_")
        }
        let afterName = name.endIndex
        if !name.isEmpty, afterName < rest.endIndex, rest[afterName] == "}" {
            // Kotlin's `vars[...] ?: m.value`: a name nobody supplied is left
            // on screen as it was written, braces and all.
            out += vars[String(name)] ?? "{\(name)}"
            rest = rest[rest.index(after: afterName)...]
        } else {
            out += "{"
            rest = rest[afterOpen...]
        }
    }
    out += rest
    return out
}

/// `wakeNote` (`ManualContent.kt:272`): "Some rooms answer to another word:
/// …", or nil when every room answers to the one being named — which is what
/// makes the `wake-word` live slot draw **nothing** in a one-word house
/// (`ManualScreen.kt:919`).
public func manualWakeNote(_ wake: ManualWakeWord, _ info: ManualWakeInfo) -> String? {
    guard !info.others.isEmpty else { return nil }
    let list = info.others
        .map { other in
            manualFill(
                wake.noteItem,
                ["phrase": other.phrase, "rooms": other.rooms.joined(separator: ", ")]
            )
        }
        .joined(separator: wake.noteJoin)
    return manualFill(wake.note, ["list": list])
}

// MARK: - links out

/// `docRef` (`ManualContent.kt:288`): the docs of the version the server runs
/// — its tag when the version label is a release **exactly** (`1.0.0`,
/// `1.0.0-rc2`), else `main`.
///
/// The label comes from `GET /api/config/version`, which proxies the core's
/// `git_version.boot_label()`: `"1.0.0"` on a release, `"1.0.0 + 14
/// (abc1234)"` past one — and that one must fall to `main`, because there is
/// no tag for a checkout fourteen commits past a release.
public func manualDocRef(_ versionLabel: String?) -> String {
    guard let label = versionLabel, isManualReleaseLabel(label) else { return "main" }
    return "v\(label)"
}

/// `^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$`, over the bytes. See this file's note
/// on why not as a regular expression.
private func isManualReleaseLabel(_ label: String) -> Bool {
    var rest = Substring(label)

    func digits() -> Bool {
        let run = rest.prefix { $0.isASCII && $0.isNumber }
        guard !run.isEmpty else { return false }
        rest = rest.dropFirst(run.count)
        return true
    }

    func dot() -> Bool {
        guard rest.first == "." else { return false }
        rest = rest.dropFirst()
        return true
    }

    guard digits(), dot(), digits(), dot(), digits() else { return false }
    if rest.isEmpty { return true }
    guard rest.first == "-" else { return false }
    rest = rest.dropFirst()
    guard !rest.isEmpty else { return false }
    return rest.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == ".") }
}

/// `docUrl` (`ManualContent.kt:289`): `doc_base` with the ref filled in, plus
/// the target. The `doc:` prefix is stripped, as the Kotlin does, so a
/// caller may pass a span's target either way.
public func manualDocURL(_ doc: ManualDoc, version: String?, target: String) -> String {
    let ref = manualDocRef(version)
    let base = doc.docBase.replacingOccurrences(of: "{ref}", with: ref)
    let path = target.hasPrefix("doc:") ? String(target.dropFirst("doc:".count)) : target
    return base + path
}

/// `dashboardUrl` (`ManualContent.kt:293`): the dashboard of the connected
/// server at `hash` (`"#settings/voices"`). Nil when there is no server, so
/// there is no button to press.
public func manualDashboardURL(_ serverURL: String?, hash: String) -> String? {
    var base = (serverURL ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    while base.hasSuffix("/") { base = String(base.dropLast()) }
    guard !base.isEmpty else { return nil }
    let fragment = hash.hasPrefix("#") ? hash : "#\(hash)"
    return "\(base)/\(fragment)"
}

// MARK: - the checklist

/// What the app read for the checklist — nil in a field is "couldn't tell"
/// and is **not** "no" (`ManualContent.kt:304`).
public struct ManualSignals: Equatable, Sendable {

    public let rooms: [ManualRoom]?
    public let roomHeard: Bool?
    public let people: Int?
    public let tracks: Int64?

    public init(
        rooms: [ManualRoom]? = nil,
        roomHeard: Bool? = nil,
        people: Int? = nil,
        tracks: Int64? = nil
    ) {
        self.rooms = rooms
        self.roomHeard = roomHeard
        self.people = people
        self.tracks = tracks
    }
}

/// One line of the checklist the app draws. Kotlin returns
/// `Pair<String, Boolean?>` (`ManualContent.kt:311`); the id comes along so a
/// view can key the row and a test can name the line it means.
public struct ManualChecklistLine: Equatable, Sendable, Identifiable {
    public let id: String
    public let text: String
    /// Nil: this app cannot tell from here, which is drawn as neither ticked
    /// nor not (`labels.checklist_unknown`).
    public let done: Bool?

    public init(id: String, text: String, done: Bool?) {
        self.id = id
        self.text = text
        self.done = done
    }
}

/// `appChecklist` (`ManualContent.kt:311`): which checklist lines the app
/// shows, in the document's order, and which are ticked.
///
/// Three outcomes per line, and the middle one is the interesting one: a line
/// the dashboard owns is dropped **unless** it carries `app_text`, which is
/// how "put the app on your phones" becomes "pair this phone" — always
/// ticked, because the app is reading the line.
public func manualAppChecklist(_ doc: ManualDoc, _ signals: ManualSignals) -> [ManualChecklistLine] {
    doc.start.checklist.compactMap { check in
        if check.platforms.contains("app") {
            return ManualChecklistLine(
                id: check.id,
                text: check.text,
                done: manualCheckDone(check.doneWhen, signals)
            )
        }
        if let appText = check.appText {
            return ManualChecklistLine(
                id: check.id,
                text: appText,
                done: check.appDoneWhen == "this_phone_paired" ? true : nil
            )
        }
        return nil
    }
}

/// `done` (`ManualContent.kt:320`). An unknown key is nil, which is what
/// keeps the two dashboard-only signals (`admin_claimed`,
/// `internet_answered`) from ever reading as "not done" on a phone.
private func manualCheckDone(_ key: String, _ signals: ManualSignals) -> Bool? {
    switch key {
    case "rooms": return signals.rooms.map { !$0.isEmpty }
    case "room_heard": return signals.roomHeard
    case "people": return signals.people.map { $0 > 0 }
    case "music": return signals.tracks.map { $0 > 0 }
    default: return nil
    }
}

// MARK: - search

/// `searchManual` (`ManualContent.kt:332`): the pieces matching **every**
/// word of the query, best first — the dashboard's `umSearch`.
///
/// `visible` is the plugin gate (``ManualModel/visiblePieces``), so a piece
/// belonging to a plugin that is not installed cannot be found any more than
/// it can be seen on the map. Aliases are excluded: they are a second name
/// for a page, not a second page.
public func searchManual(
    _ doc: ManualDoc,
    visible: Set<String>,
    query: String,
    max limit: Int = 8
) -> [ManualPiece] {
    let words = manualWords(query.lowercased())
    guard !words.isEmpty else { return [] }

    var scored: [(piece: ManualPiece, score: Int, at: Int)] = []
    for (at, piece) in doc.pieces.enumerated() {
        guard visible.contains(piece.id), piece.aliasOf == nil else { continue }
        let title = piece.title.lowercased()
        let keywords = piece.keywords.map { $0.lowercased() }
        let hay = ([title, piece.subtitle.lowercased()] + keywords
            + [doc.topic(piece.topic)?.title.lowercased() ?? ""])
            .joined(separator: " \u{00B7} ")
        guard words.allSatisfy({ hay.contains($0) }) else { continue }
        let score = words.reduce(0) { total, word in
            if title.hasPrefix(word) { return total + 4 }
            if title.contains(word) { return total + 3 }
            if keywords.contains(where: { $0.hasPrefix(word) }) { return total + 2 }
            return total + 1
        }
        scored.append((piece, score, at))
    }
    return scored
        .sorted { lhs, rhs in
            if lhs.score != rhs.score { return lhs.score > rhs.score }
            if lhs.piece.title != rhs.piece.title { return lhs.piece.title < rhs.piece.title }
            // Kotlin's `sortedWith` is stable, so two pieces with the same
            // score and the same title keep the document's order. Swift's
            // sort is not stable, so the order is part of the comparator.
            return lhs.at < rhs.at
        }
        .prefix(limit)
        .map(\.piece)
}

/// Kotlin's `split(Regex("\\s+")).filter { it.isNotEmpty() }`, over Java's
/// ASCII `\s` rather than ICU's Unicode one. See this file's note.
private func manualWords(_ text: String) -> [String] {
    text
        .split(whereSeparator: { character in
            character == " " || character == "\t" || character == "\n"
                || character == "\r" || character == "\u{000B}" || character == "\u{000C}"
        })
        .map(String.init)
}

// MARK: - the handlers

/// `phrasesByHandler` (`ManualContent.kt:359`): the manual's own phrases per
/// handler, for the handlers the server lists with no `example_phrases` of
/// their own.
///
/// Never a `pending` phrase (this server may not answer to it), never a
/// `then` (that is a reply, not something to say), and never one written with
/// an ellipsis — `"my name is …"` is a shape, not a sentence a person can
/// repeat.
public func manualPhrasesByHandler(_ doc: ManualDoc) -> [String: [String]] {
    var out: [String: [String]] = [:]
    let blocks = [doc.start.blocks] + doc.pieces.map(\.blocks)
    for block in blocks.flatMap({ $0 }) where block.type == "say" {
        for phrase in block.phrases {
            guard let handler = phrase.handler else { continue }
            guard phrase.pending == nil, !phrase.then, !phrase.text.contains("\u{2026}") else {
                continue
            }
            var list = out[handler] ?? []
            if !list.contains(phrase.text) {
                list.append(phrase.text)
                out[handler] = list
            }
        }
    }
    return out
}

/// `startPhrase` (`ManualContent.kt:373`): the phrase Home's first-run hint
/// offers — Start here's first real one.
///
/// Home does not read it today: `HomeView.swift:283` passes
/// `HomeHint(phrase: nil)` with a comment saying the manual's phrase is not
/// read on iOS. This is what would make it readable; wiring it is a change to
/// Home's own file and is written up in the item's DECISIONS.md instead.
public func manualStartPhrase(_ doc: ManualDoc?) -> String? {
    doc?.start.blocks
        .first { $0.type == "say" && !$0.phrases.isEmpty }?
        .phrases
        .first { $0.pending == nil && !$0.then }?
        .text
}

/// `listedHandlers` (`ManualContent.kt:381`): the handlers "Everything it can
/// do" lists.
///
/// A handler named in `handler_needs_feature` is listed only while the server
/// has that feature **on** — note the asymmetry with
/// ``shownManualPhrases(_:features:)``, which drops a phrase only when the
/// feature is explicitly false. It is the Kotlin's, and it is deliberate
/// there: a phrase is a suggestion, a handler in the list is a claim.
public func manualListedHandlers(
    _ doc: ManualDoc,
    _ names: [String],
    features: [String: Bool]
) -> [String] {
    names.filter { name in
        guard let feature = doc.handlerNeedsFeature[name] else { return true }
        return features[feature] == true
    }
}

// MARK: - a say block's rows

/// One row of a `say` block: a phrase, and the reply it gets where the
/// document gives one.
public struct ManualSayRow: Equatable, Sendable, Identifiable {
    public let phrase: ManualPhrase
    /// The `then` phrase that followed this one, if any.
    public let then: ManualPhrase?

    /// Stable within a block: the phrase's text, which is what a reader sees
    /// and what the document guarantees is distinct inside one block.
    public var id: String { phrase.text }

    public init(phrase: ManualPhrase, then: ManualPhrase? = nil) {
        self.phrase = phrase
        self.then = then
    }
}

/// The rows a `say` block draws (`ManualScreen.kt:808`): the phrases this
/// server answers, each paired with the `then` that follows it.
///
/// The pairing happens **after** the feature filter, which is the order the
/// Kotlin does it in and the order that matters: a phrase dropped because its
/// feature is off takes its reply with it, and the phrase above it does not
/// inherit that reply. A view that filtered afterwards would caption one
/// phrase with another's answer.
public func manualSayRows(_ phrases: [ManualPhrase], features: [String: Bool]) -> [ManualSayRow] {
    let shown = shownManualPhrases(phrases, features: features)
    var rows: [ManualSayRow] = []
    for (at, phrase) in shown.enumerated() where !phrase.then {
        let following = at + 1 < shown.count ? shown[at + 1] : nil
        rows.append(ManualSayRow(phrase: phrase, then: following?.then == true ? following : nil))
    }
    return rows
}

/// A room's second line in the `rooms` live slot: status, the word it answers
/// to, and what it is playing (`ManualScreen.kt:957`).
///
/// **The third part is what Android cannot show** — see ``ManualRoom/song``.
public func manualRoomLine(_ room: ManualRoom, _ wake: ManualWakeWord) -> String {
    let word = manualWakePhrase(wake, room).map { manualFill(wake.quoted, ["phrase": $0]) }
    return ManualStrings.roomLine([room.status, word, room.song])
}

/// `manualRouteOpens` (`ManualContent.kt:389`): whether a manual link to this
/// route opens something **here**.
///
/// A link to an app screen this server does not offer reads as plain text,
/// never as a link that lands back on Home. A route with no app screen at all
/// opens only if it opens the dashboard.
///
/// The signature is the one place a rule had to change shape. Kotlin's
/// `Route.visibleWith(caps)` takes the capability manifest; iOS's takes
/// ``RouteVisibility`` — capabilities plus `sharedScreen` and `phoneCanChat`
/// (`Route.swift:150`) — because Chat's visibility on this platform depends
/// on whether this phone can chat at all. So this takes the visibility, and
/// `Route.swift` is untouched.
public func manualRouteOpens(_ route: ManualRoute, _ visibility: RouteVisibility) -> Bool {
    if route.app.dashboard { return true }
    guard let name = route.app.screen else { return false }
    // An unknown screen name opens: Kotlin's `?: return true`. A document
    // newer than the app names a screen this build has never heard of, and
    // the link is left alive rather than quietly deadened — the app will show
    // its own "that is not a screen here" rather than pretending the sentence
    // never had a link in it.
    guard let screen = Route(rawValue: name) else { return true }
    return screen.visibleWith(visibility)
}

// MARK: - the inline wash, as numbers

/// One **opaque** sRGB colour, as the three components a contrast ratio is
/// computed from.
///
/// It exists so the wash under an inline `code` run can be *stated*: the view
/// builds its `Color` from these numbers and
/// ``manualContrastRatio(_:_:)`` computes the ratios the doc comment claims,
/// so the claim and the pixels cannot drift apart. A `Color` cannot do that —
/// `Color.fgMuted` and friends live in the app target, and a translucency over
/// an unknown parent has no ratio at all, which is why there is no `alpha`
/// here.
public struct ManualWash: Equatable, Sendable {

    public let red: Double
    public let green: Double
    public let blue: Double

    public init(red: Double, green: Double, blue: Double) {
        self.red = red
        self.green = green
        self.blue = blue
    }

    /// From the hex the token is written as in
    /// `android/.../ui/theme/Color.kt` and
    /// `web/static/colors_and_type.css`.
    public init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}

/// `--sunken` in the light theme (`#F5F3F0`, `Color.kt:49`) — "table header,
/// code blocks" in the stylesheet's own words
/// (`web/static/colors_and_type.css:138`), which is exactly what an inline
/// `code` run is.
public let manualCodeWashLight = ManualWash(hex: 0xF5F3F0)

/// `--sunken` in the dark theme (`#100E0B`, `Color.kt:77`).
public let manualCodeWashDark = ManualWash(hex: 0x100E0B)

/// WCAG 2.1 relative luminance.
public func manualRelativeLuminance(_ wash: ManualWash) -> Double {
    func channel(_ value: Double) -> Double {
        value <= 0.04045 ? value / 12.92 : pow((value + 0.055) / 1.055, 2.4)
    }
    return 0.2126 * channel(wash.red)
        + 0.7152 * channel(wash.green)
        + 0.0722 * channel(wash.blue)
}

/// The WCAG contrast ratio between two opaque colours, lighter over darker.
///
/// Body text needs 4.5:1. Every pairing the manual draws on
/// ``manualCodeWashLight``/``manualCodeWashDark`` is listed in
/// `ManualPageView.swift`'s `Color.manualCodeWash`, and this is the function
/// those numbers come out of.
public func manualContrastRatio(_ one: ManualWash, _ other: ManualWash) -> Double {
    let a = manualRelativeLuminance(one)
    let b = manualRelativeLuminance(other)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)
}

/// The wash under a **map node's** folded-in `preview`/`plugin` word, light
/// theme.
///
/// Not ``manualCodeWashLight``, and the difference is the whole point of this
/// token existing: a map node's card is `secondarySystemBackground`
/// (`#F2F2F7`), and `--sunken` on that is **1.008:1** — a wash nobody can
/// see, which would have made the pills read as plain words with extra spaces
/// around them. Android does not have that problem because it washes a node's
/// tags with `idleSoft` over `card`, a different token from the `sunken` it
/// washes a page's code runs with (`ManualScreen.kt:668` against `:872`).
///
/// So this is Android's own node wash, composited **once, by hand**, onto the
/// surface this screen actually draws a node on: `idleSoft` is `#A09E9B` at
/// α=0.141 (`Color.kt:64`) and over `#F2F2F7` that is `#E6E6EA`. The result
/// is **1.115:1** against the card, which is Android's own 1.127:1 to within
/// a rounding step — a tint you can see, with a ratio that can be stated
/// because it is opaque.
public let manualNodeWashLight = ManualWash(hex: 0xE6E6EA)

/// The same, dark: `idleSoft` is `#73716E` at α=0.180 (`Color.kt:92`) and over
/// `secondarySystemBackground`'s `#1C1C1E` that is `#2C2B2C` — **1.206:1**
/// against the card, where Android's is 1.209:1.
public let manualNodeWashDark = ManualWash(hex: 0x2C2B2C)

// MARK: - the name that scrambles into place, as arithmetic

/// The alphabet a scrambling name is drawn from (`GLYPHS`,
/// `ManualScreen.kt:169`), character for character.
public let manualScrambleGlyphs = Array("ABCDEFGHKMNPRSTVXZ0123456789#%&*/<>")

/// Nine frames, `0...8` (`ScrambleText`'s `steps = 8`, `:688`).
public let manualScrambleSteps = 8

/// One frame of a name scrambling into place (`ScrambleText`'s `buildString`,
/// `ManualScreen.kt:690-694`), as a function of the frame number.
///
/// It lives here rather than inside the view's loop so that it can be *tested*
/// — the settle arithmetic is the part of answer 30 that can be wrong without
/// anybody noticing on a screenshot, and the view (`ManualMapView.swift`'s
/// ``ManualScrambleName``) calls exactly this.
///
///  * the name settles **from the left**, `frame / steps` of it at a time,
///    which is the Kotlin's `i < frame.toFloat() / steps * text.length`;
///  * a **space is never replaced**, so the word shapes stay where they are;
///  * the character **count never changes**, at any frame.
///
/// `glyph` is the picker — `{ manualScrambleGlyphs.randomElement()! }` in the
/// view, a constant in a test, which is the only way an effect built on
/// `Random` can be asserted on at all.
public func manualScrambleFrame(
    of letters: [Character],
    frame: Int,
    steps: Int = manualScrambleSteps,
    glyph: () -> Character
) -> String {
    let steps = max(steps, 1)
    let frame = min(max(frame, 0), steps)
    let settled = Int(Double(frame) / Double(steps) * Double(letters.count))
    return String(
        letters.enumerated().map { at, letter in
            at < settled || letter == " " ? letter : glyph()
        }
    )
}

// MARK: - the float, as arithmetic

/// How long one bob takes for the card at this index in the map's geometry:
/// `5.5 + (i % 4) * 0.9` seconds (`bob`, `ManualScreen.kt:561`).
public func manualFloatPeriod(at index: Int) -> Double {
    5.5 + Double(index % 4) * 0.9
}

/// How far into its bob a card starts — Android's `i * 0.7` seconds
/// (`:562`), **wrapped at eight**.
///
/// Android's phase is an offset inside a sine that is already running, so an
/// unbounded `i * 0.7` only ever picks a point on the curve. Here it is a
/// one-shot `delay` on a repeating animation, so it is the time the card
/// spends *still* before its first bob: uncapped, the twentieth card of a
/// larger geometry would sit motionless for fourteen seconds after the map
/// opened, which reads as a bug rather than as a stagger. Wrapped at eight it
/// is at most 4.9 s, under one of the 5.5–8.2 s periods, and the ring of seven
/// nodes a phone draws is unaffected either way.
public func manualFloatPhase(at index: Int) -> Double {
    Double(index % 8) * 0.7
}
