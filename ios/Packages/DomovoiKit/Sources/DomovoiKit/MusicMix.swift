import Foundation
import Observation

/// The Music mix (Up next 1.3.0): how Up next picks the songs that follow.
/// The port of Android's `mix/MusicMix.kt` (8230ccd2); the server's routes
/// are `web/backend/api/music_mix.py`, the words and the layout are the
/// dashboard's (`web/static/music_mix.jsx` MusicMixPanel and PersonMusicMix).
/// This file is the wire shapes, the words, who may change what, the calls,
/// and the two models the screens draw from.
///
/// Who may change what (wf/mix-gate, 2026-11-14):
/// - the household's mix (`PUT /api/music/mix`): an admin only — a phone's
///   household token gets 401 "admin session required";
/// - a person's own sliders (`PUT /api/people/{id}/music-mix`): an admin, or
///   the device whose `owner_person_id` is that person (a plain owner match by
///   the `X-Device-Id` the caller names itself by); anyone else 403
///   (``MusicMixStrings/notYours``);
/// - the two internet switches: an admin (`PATCH /api/config/editable`).
///
/// This app holds no admin session (``musicMixAppIsAdmin``), so on a phone the
/// household's mix and the internet switches are said in words, and only the
/// person this phone is ("this is me") gets sliders.

// MARK: - the wire

/// `internet`: the two ListenBrainz switches as the core last reported them
/// (nil: not reported yet), and its fetch state.
public struct MixInternet: Equatable, Sendable, Decodable {
    public let similarArtists: Bool?
    public let popularity: Bool?
    public let state: String

    public init(similarArtists: Bool? = nil, popularity: Bool? = nil, state: String = "unknown") {
        self.similarArtists = similarArtists
        self.popularity = popularity
        self.state = state
    }

    private enum CodingKeys: String, CodingKey {
        case popularity, state
        case similarArtists = "similar_artists"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        similarArtists = try? values.decodeIfPresent(Bool.self, forKey: .similarArtists)
        popularity = try? values.decodeIfPresent(Bool.self, forKey: .popularity)
        state = (try? values.decodeIfPresent(String.self, forKey: .state)) ?? "unknown"
    }
}

/// `GET /api/music/mix` (`music_mix.py` `_mix_answer`: Mix.json() + steps,
/// range, internet, people_with_own_mix). Every field lenient, each with the
/// server's own default.
public struct MusicMix: Equatable, Sendable, Decodable {
    public let discoveryFavorites: Int
    public let closeWander: Int
    public let autoplay: Bool
    public let listRepeatMin: Int
    public let steps: [Int]
    public let listRepeatMinRange: [Int]
    public let internet: MixInternet
    public let peopleWithOwnMix: [Int]

    public init(
        discoveryFavorites: Int = MusicMixRules.middle,
        closeWander: Int = MusicMixRules.middle,
        autoplay: Bool = true,
        listRepeatMin: Int = 50,
        steps: [Int] = [1, 2, 3, 4, 5],
        listRepeatMinRange: [Int] = [20, 500],
        internet: MixInternet = MixInternet(),
        peopleWithOwnMix: [Int] = []
    ) {
        self.discoveryFavorites = discoveryFavorites
        self.closeWander = closeWander
        self.autoplay = autoplay
        self.listRepeatMin = listRepeatMin
        self.steps = steps
        self.listRepeatMinRange = listRepeatMinRange
        self.internet = internet
        self.peopleWithOwnMix = peopleWithOwnMix
    }

    private enum CodingKeys: String, CodingKey {
        case autoplay, steps, internet
        case discoveryFavorites = "discovery_favorites"
        case closeWander = "close_wander"
        case listRepeatMin = "list_repeat_min"
        case listRepeatMinRange = "list_repeat_min_range"
        case peopleWithOwnMix = "people_with_own_mix"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let middle: Int = MusicMixRules.middle
        discoveryFavorites = (try? values.decodeIfPresent(Int.self, forKey: .discoveryFavorites)) ?? middle
        closeWander = (try? values.decodeIfPresent(Int.self, forKey: .closeWander)) ?? middle
        autoplay = (try? values.decodeIfPresent(Bool.self, forKey: .autoplay)) ?? true
        listRepeatMin = (try? values.decodeIfPresent(Int.self, forKey: .listRepeatMin)) ?? 50
        steps = (try? values.decodeIfPresent([Int].self, forKey: .steps)) ?? [1, 2, 3, 4, 5]
        listRepeatMinRange = (try? values.decodeIfPresent([Int].self, forKey: .listRepeatMinRange)) ?? [20, 500]
        internet = (try? values.decodeIfPresent(MixInternet.self, forKey: .internet)) ?? MixInternet()
        peopleWithOwnMix = (try? values.decodeIfPresent([Int].self, forKey: .peopleWithOwnMix)) ?? []
    }
}

/// A person's two sliders: `override` (either may be missing in a stored
/// override, `mix.py` person_override) and `uses` (always both).
public struct MixSliders: Equatable, Sendable, Decodable {
    public let discoveryFavorites: Int?
    public let closeWander: Int?

    public init(discoveryFavorites: Int? = nil, closeWander: Int? = nil) {
        self.discoveryFavorites = discoveryFavorites
        self.closeWander = closeWander
    }

    private enum CodingKeys: String, CodingKey {
        case discoveryFavorites = "discovery_favorites"
        case closeWander = "close_wander"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        discoveryFavorites = try? values.decodeIfPresent(Int.self, forKey: .discoveryFavorites)
        closeWander = try? values.decodeIfPresent(Int.self, forKey: .closeWander)
    }
}

/// `GET/PUT /api/people/{id}/music-mix` (`music_mix.py` `_person_answer`).
public struct PersonMix: Equatable, Sendable, Decodable {
    public let personID: Int
    /// nil: "Use the household’s mix".
    public let override: MixSliders?
    /// What that person's sessions use now: theirs, else the household's.
    public let uses: MixSliders

    public init(personID: Int, override: MixSliders? = nil, uses: MixSliders = MixSliders()) {
        self.personID = personID
        self.override = override
        self.uses = uses
    }

    /// They have sliders of their own.
    public var ownMix: Bool { override != nil }

    private enum CodingKeys: String, CodingKey {
        case override, uses
        case personID = "person_id"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        personID = try values.decode(Int.self, forKey: .personID)
        override = try? values.decodeIfPresent(MixSliders.self, forKey: .override)
        uses = (try? values.decodeIfPresent(MixSliders.self, forKey: .uses)) ?? MixSliders()
    }
}

// MARK: - the words (music_mix.jsx; MusicMix.kt)

/// One slider: its label, its two ends, and each step said in words.
public struct MixSliderSpec: Equatable, Sendable {
    public let label: String
    public let left: String
    public let right: String
    public let words: [Int: String]

    /// The step in words; nil or a step out of range is the middle.
    public func said(_ step: Int?) -> String {
        let at: Int = step ?? MusicMixRules.middle
        return words[at] ?? words[MusicMixRules.middle] ?? ""
    }
}

public enum MusicMixStrings {
    public static let discovery = MixSliderSpec(
        label: "discovery or favorites", left: "Discovery", right: "Favorites",
        words: [
            1: "mostly songs you haven’t heard in a while",
            2: "more songs you haven’t heard in a while",
            3: "a balance (the default)",
            4: "more of what you play most",
            5: "mostly what you play most",
        ]
    )

    public static let wander = MixSliderSpec(
        label: "stay close or wander", left: "Stay close", right: "Wander",
        words: [
            1: "stays close to the song playing",
            2: "mostly close to the song playing",
            3: "a balance (the default)",
            4: "wanders more across your library",
            5: "wanders widely across your library",
        ]
    )

    public static let intro = "How Up next picks the songs that follow when the queue runs out — in every room, browser and phone. "
        + "Up next leans a little toward songs you haven’t heard in a while; each song in the queue can tell you why it was picked."
    public static let mixTitle = "The mix"
    public static let mixSub = "People can set their own in Settings → People; everyone else gets these."
    public static let playingOnTitle = "Playing on"
    public static let autoplayLabel = "Autoplay when a playlist ends"
    public static let autoplaySub = "Similar songs keep playing when the queue runs out. Rooms and new players start this way; "
        + "a browser or phone can still turn it off for itself in its queue."
    public static let noRepeatsLabel = "No repeats within"
    public static let noRepeatsSub = "A list shorter than this plays once, then similar songs follow. A longer one starts over, shuffled again."
    public static let internetTitle = "From the internet"
    public static let internetSub = "Optional. Up next works from your library alone without them."
    public static let similarArtistsLabel = "Similar artists from the internet"
    public static let similarArtistsSub = "Up next can play songs you have by artists like the one playing. Asks ListenBrainz with your "
        + "artists’ MusicBrainz ids only — no names, nothing you played. Up next never waits for it."
    public static let popularityLabel = "Song popularity from the internet"
    public static let popularitySub = "An artist’s well-known songs come up a little more often (your own taste counts more). "
        + "Asks ListenBrainz with your songs’ MusicBrainz ids only."

    /// Under the household's mix on a phone (no admin session here).
    public static let adminCanChangeThis = "An admin can change this."
    /// Under the internet switches on a phone.
    public static let adminCanChangeThese = "An admin can change these."

    public static let notKnownYet = "not known yet"
    public static let useHouseholds = "Use the household’s mix"
    public static let personHead = "music mix"
    public static let openOnTheDashboard = "Open on the dashboard"
    public static let yourMix = "Your mix"
    public static let reading = "reading…"
    public static let notPaired =
        "Pair this phone with your home server to see its music mix (Settings → Phones & browsers)."
    public static let olderServer =
        "This home server is older than the music mix. Update Domovoi to choose how Up next picks songs."

    /// The server's 403 for someone else's mix (`music_mix.py`
    /// NOT_YOURS_DETAIL); shown only when a 403 carries no words of its own.
    public static let notYours = "Only an admin, or this person's own phone, can change their mix."

    public static func readOnlyNote(_ name: String) -> String { "An admin, or \(name)’s own phone, can change this." }
    public static func usedWhen(_ name: String) -> String {
        "Used when Up next plays for \(name): “listening as” them, their phone, or their voice in a room."
    }
    public static func usesHouseholds(_ name: String) -> String { "\(name) uses the household’s mix." }
    public static func hasOwn(_ name: String) -> String { "\(name) has their own mix." }
    public static func usesHouseholdsToast(_ name: String) -> String { "\(name) uses the household’s mix" }
    public static func hasOwnToast(_ name: String) -> String { "\(name) has their own mix" }
    public static func savedToast(_ name: String) -> String { "\(name)’s mix saved" }
    public static func yourMixSub(_ name: String) -> String {
        "\(name.trimmingCharacters(in: .whitespaces).isEmpty ? "Your" : name)’s own sliders, if you want them."
    }
    /// VoiceOver's name for the switch on a person's mix.
    public static func useHouseholdsFor(_ name: String) -> String { "\(useHouseholds) for \(name)" }
    /// VoiceOver's name for a slider.
    public static func sliderName(_ spec: MixSliderSpec) -> String { "\(spec.left) or \(spec.right)" }
    public static func noRepeatsValue(_ n: Int) -> String { "\(n) songs" }
    public static func onOff(_ value: Bool?) -> String {
        switch value {
        case .some(true): return "on"
        case .some(false): return "off"
        case .none: return notKnownYet
        }
    }

    /// Under the household's Autoplay default: what THIS phone does. nil
    /// `choice`: it follows the household's; else what was chosen in its queue.
    public static func phoneAutoplayNote(_ choice: Bool?) -> String {
        switch choice {
        case .none: return "This phone follows this until you switch autoplay in its queue."
        case .some(true): return "This phone: autoplay on (switched in its queue)."
        case .some(false): return "This phone: autoplay off (switched in its queue)."
        }
    }

    /// What to say when a mix can't be read.
    public static func readProblem(_ error: DomovoiError) -> String {
        if error.failure?.status == 404 { return olderServer }
        return "couldn’t read the music mix: \(error.message)"
    }
}

// MARK: - who may change what, and whose sliders win

/// This app never signs in as an admin (Settings → Advanced is the
/// dashboard's): every admin-only write is left out on a phone.
public let musicMixAppIsAdmin = false

/// What a Music mix screen offers for writing; anything false is shown as
/// words, never as a control the server would refuse.
public struct MixRights: Equatable, Sendable {
    public let household: Bool
    public let internet: Bool

    public init(household: Bool, internet: Bool) {
        self.household = household
        self.internet = internet
    }
}

public enum MusicMixRules {
    public static let middle = 3

    public static func rights(admin: Bool = musicMixAppIsAdmin) -> MixRights {
        MixRights(household: admin, internet: admin)
    }

    /// Whether this phone may change `personID`'s own sliders: an admin, or
    /// the phone whose owner (the server's `owner_person_id`, "this is me")
    /// is that person. A shared screen is nobody's. The server's own check
    /// is the same plain owner match, and a 403 (the claim changed
    /// meanwhile) is still said in its words.
    public static func editable(personID: Int, owner: DeviceOwner?, shared: Bool, admin: Bool = musicMixAppIsAdmin) -> Bool {
        if admin { return true }
        guard !shared, let owner else { return false }
        return owner.id == personID
    }

    /// What Up next's Autoplay is on this phone: what the person chose here
    /// (nil until they flip it), else the household's Music mix default (nil
    /// until read), else on (the server's own default, `mix.py` Mix.autoplay).
    public static func effectiveAutoplay(choice: Bool?, householdDefault: Bool?) -> Bool {
        if let choice { return choice }
        if let householdDefault { return householdDefault }
        return true
    }

    /// Turning "Use the household’s mix" off: start from what they use now
    /// (the web's put({…uses || 3})).
    public static func ownMix(from uses: MixSliders) -> MixSliders {
        MixSliders(discoveryFavorites: uses.discoveryFavorites ?? middle, closeWander: uses.closeWander ?? middle)
    }

    /// One slider moved: the other stays as it is used now.
    public static func withStep(_ uses: MixSliders, discovery: Bool, step: Int) -> MixSliders {
        let base = ownMix(from: uses)
        return discovery
            ? MixSliders(discoveryFavorites: step, closeWander: base.closeWander)
            : MixSliders(discoveryFavorites: base.discoveryFavorites, closeWander: step)
    }

    /// `PUT /api/people/{id}/music-mix`'s body: the two sliders, or null for
    /// "Use the household’s mix" — never the household's numbers copied.
    public static func personBody(_ override: MixSliders?) -> Data {
        guard let override else { return Data(#"{"override":null}"#.utf8) }
        let d: Int = override.discoveryFavorites ?? middle
        let c: Int = override.closeWander ?? middle
        return Data(#"{"override":{"discovery_favorites":\#(d),"close_wander":\#(c)}}"#.utf8)
    }

    /// What to say when a mix write was refused: the server's own words for
    /// a 403 (``MusicMixStrings/notYours`` when it gave none); for a 401 that
    /// is not about the household token (the admin gate's "admin session
    /// required"), ``MusicMixStrings/adminCanChangeThis`` — never a lost
    /// pairing. nil for anything else (the caller's usual failure line).
    public static func writeRefusal(_ error: DomovoiError) -> String? {
        guard let f = error.failure else { return nil }
        if isDeviceTokenRefusal(status: f.status, body: f.body) { return nil }
        if f.status == 403 { return claimDetail(error) ?? MusicMixStrings.notYours }
        if f.status == 401 { return MusicMixStrings.adminCanChangeThis }
        return nil
    }
}

// MARK: - the household's Autoplay default, by server

/// The household's Autoplay default as each server last answered it, kept by
/// server (normalised) so one house's answer never shows on another's switch.
/// Memory only: read again whenever the queue or the Music mix screen is on
/// screen (`MixDefaults`, MusicMix.kt).
@MainActor
@Observable
public final class MixDefaults {
    public private(set) var byServer: [String: Bool] = [:]

    public init() {}

    public func autoplayDefault(for server: String) -> Bool? {
        byServer[ServerCredentials.normalize(server)]
    }

    /// `server`'s answer: the server the read was SENT to, captured before it.
    public func heard(_ server: String, _ mix: MusicMix) {
        byServer[ServerCredentials.normalize(server)] = mix.autoplay
    }
}

// MARK: - the calls

public enum MusicMixPath {
    public static let household = "/api/music/mix"
    public static func person(_ id: Int) -> String { "/api/people/\(id)/music-mix" }
}

/// `GET /api/music/mix` from `server` only (sent nowhere else).
public func readHouseholdMix(client: DomovoiClient, server: String) async throws(DomovoiError) -> MusicMix {
    let data = try await client.raw(method: "GET", path: MusicMixPath.household, onlyTo: server)
    return try DomovoiClient.decode(data, as: MusicMix.self)
}

/// `GET /api/people/{id}/music-mix` from `server` only.
public func readPersonMix(client: DomovoiClient, server: String, personID: Int) async throws(DomovoiError) -> PersonMix {
    let data = try await client.raw(method: "GET", path: MusicMixPath.person(personID), onlyTo: server)
    return try DomovoiClient.decode(data, as: PersonMix.self)
}

/// `PUT /api/people/{id}/music-mix` to `server` only, naming this phone
/// (`X-Device-Id`: the server matches its owner against `personID`).
public func savePersonMix(
    client: DomovoiClient,
    server: String,
    deviceID: String,
    personID: Int,
    override: MixSliders?
) async throws(DomovoiError) -> PersonMix {
    let data = try await client.raw(
        method: "PUT",
        path: MusicMixPath.person(personID),
        body: MusicMixRules.personBody(override),
        headers: [deviceIDHeader: deviceID],
        onlyTo: server
    )
    return try DomovoiClient.decode(data, as: PersonMix.self)
}

// MARK: - the household's mix, for Settings → Music mix

/// The household's mix as Settings → Music mix draws it: read for the server
/// the app talks to, and shown only while it still talks to that one
/// ([[read-after-await-wrong-key]]). Every answer also feeds
/// ``MixDefaults`` under the server it was asked of.
@MainActor
@Observable
public final class HouseholdMixModel {

    public struct Answer: Equatable, Sendable {
        public let server: String
        public let mix: MusicMix?
        public let problem: String?
    }

    public private(set) var answer: Answer?

    @ObservationIgnored private let client: DomovoiClient
    @ObservationIgnored private let server: @MainActor () -> String
    @ObservationIgnored private let defaults: MixDefaults

    public init(client: DomovoiClient, server: @escaping @MainActor () -> String, defaults: MixDefaults) {
        self.client = client
        self.server = server
        self.defaults = defaults
    }

    private var now: String { ServerCredentials.normalize(server()) }

    /// The mix of the server the app talks to now; nil before its answer.
    public var mix: MusicMix? {
        guard let answer, answer.server == now else { return nil }
        return answer.mix
    }

    /// Why it couldn't be read, for the server the app talks to now.
    public var problem: String? {
        guard let answer, answer.server == now else { return nil }
        return answer.problem
    }

    public func read() async {
        let at = now
        guard !at.isEmpty else { return }
        do throws(DomovoiError) {
            let mix = try await readHouseholdMix(client: client, server: at)
            defaults.heard(at, mix)
            guard at == now else { return }
            answer = Answer(server: at, mix: mix, problem: nil)
        } catch {
            guard at == now else { return }
            // A failed re-read keeps the last answer drawn, and says nothing
            // over it; the first read's failure is said.
            if answer?.server == at, answer?.mix != nil { return }
            answer = Answer(server: at, mix: nil, problem: MusicMixStrings.readProblem(error))
        }
    }
}

// MARK: - a person's own mix

/// A person's own music mix (the dashboard's PersonMusicMix; Android's
/// `PersonMusicMixSection`): "Use the household’s mix", and when off, their
/// two sliders. Every answer is kept with the server AND the person it was
/// asked for, and drawn only while both are still the ones on screen.
@MainActor
@Observable
public final class PersonMixModel {

    public struct Answer: Equatable, Sendable {
        public let server: String
        public let personID: Int
        public let mix: PersonMix?
        public let problem: String?
    }

    public private(set) var personID: Int?
    public private(set) var answer: Answer?
    /// A save on its way: the switch and sliders wait for it.
    public private(set) var busy = false
    /// The last refused save, in the server's words, for the person and the
    /// server it was for.
    public private(set) var refused: (server: String, personID: Int, text: String)?

    @ObservationIgnored private let client: DomovoiClient
    @ObservationIgnored private let server: @MainActor () -> String
    @ObservationIgnored private let deviceID: @MainActor () -> String?
    @ObservationIgnored private let say: @MainActor (String) -> Void

    public init(
        client: DomovoiClient,
        server: @escaping @MainActor () -> String,
        deviceID: @escaping @MainActor () -> String?,
        say: @escaping @MainActor (String) -> Void
    ) {
        self.client = client
        self.server = server
        self.deviceID = deviceID
        self.say = say
    }

    private var now: String { ServerCredentials.normalize(server()) }

    /// Point it at `personID`. The same person keeps what is there.
    public func show(_ personID: Int) {
        guard self.personID != personID else { return }
        self.personID = personID
        refused = nil
    }

    private func current(_ at: String, _ person: Int) -> Bool { at == now && person == personID }

    /// What is drawn: the answer for the server and the person on screen.
    public var mix: PersonMix? {
        guard let answer, current(answer.server, answer.personID) else { return nil }
        return answer.mix
    }

    public var problem: String? {
        guard let answer, current(answer.server, answer.personID) else { return nil }
        return answer.problem
    }

    public var refusal: String? {
        guard let refused, current(refused.server, refused.personID) else { return nil }
        return refused.text
    }

    public func read() async {
        guard let person = personID else { return }
        let at = now
        guard !at.isEmpty else { return }
        do throws(DomovoiError) {
            let mix = try await readPersonMix(client: client, server: at, personID: person)
            guard current(at, person) else { return }
            answer = Answer(server: at, personID: person, mix: mix, problem: nil)
        } catch {
            guard current(at, person) else { return }
            if answer?.server == at, answer?.personID == person, answer?.mix != nil { return }
            answer = Answer(server: at, personID: person, mix: nil, problem: MusicMixStrings.readProblem(error))
        }
    }

    /// The switch: on goes back to the household's mix; off starts their own
    /// from what they use now.
    public func useHousehold(_ on: Bool, name: String) async {
        guard let pm = mix else { return }
        if on {
            await save(nil, said: MusicMixStrings.usesHouseholdsToast(name))
        } else {
            await save(MusicMixRules.ownMix(from: pm.uses), said: MusicMixStrings.hasOwnToast(name))
        }
    }

    /// One slider moved to `step`.
    public func setStep(discovery: Bool, step: Int, name: String) async {
        guard let pm = mix else { return }
        await save(MusicMixRules.withStep(pm.uses, discovery: discovery, step: step), said: MusicMixStrings.savedToast(name))
    }

    /// The PUT, to the server on screen at the press, naming this phone. The
    /// answer is drawn (and its toast said) only while that server and that
    /// person are still the ones on screen; a refusal is said in the server's
    /// words, and a 403 reads the mix again (the phone stopped being theirs).
    public func save(_ override: MixSliders?, said: String) async {
        guard !busy, let person = personID, let device = deviceID() else { return }
        let at = now
        guard !at.isEmpty else { return }
        busy = true
        refused = nil
        defer { busy = false }
        do throws(DomovoiError) {
            let answered = try await savePersonMix(
                client: client, server: at, deviceID: device, personID: person, override: override
            )
            guard current(at, person) else { return }
            answer = Answer(server: at, personID: person, mix: answered, problem: nil)
            say(said)
        } catch {
            guard current(at, person) else { return }
            refused = (at, person, MusicMixRules.writeRefusal(error) ?? failureText("save", error))
            if error.failure?.status == 403 {
                busy = false
                await read()
            }
        }
    }
}

// MARK: - "why this song?" (the web's UpNextWhy; Android UpNextWhy.kt)

/// "Why this song?" on an Up next pick (1.3.0): the chip is the pick's short
/// reason (`reason_label`: "same artist", "90s"); with a `why` — the server's
/// plain sentence from the signals the picker really used — the chip is a
/// button that shows or hides that sentence as one muted line under the row,
/// and never starts the row's own action (play now). No `why` (a pick from
/// before 1.3.0): just the chip. The phone never words a reason itself: the
/// line is the server's text, as it came.
public struct WhyChip: Equatable, Sendable {
    /// On the chip.
    public let text: String
    /// The line a tap shows; nil for a plain chip.
    public let why: String?

    public init(text: String, why: String?) {
        self.text = text
        self.why = why
    }

    public var tappable: Bool { why != nil }

    /// VoiceOver's name for it (the web's aria-label).
    public var description: String {
        tappable ? "\(WhyChip.whyThisSong) \(text)".trimmingCharacters(in: .whitespaces) : text
    }

    public static let whyThisSong = "why this song?"
    /// The chip's word when the pick has a sentence but no label.
    public static let noLabel = "why?"
    public static let show = "show why"
    public static let hide = "hide why"
    public static let shown = "shown"
    public static let hidden = "hidden"

    /// The chip for a pick with `label` and `why`; nil when it has neither.
    public static func of(label: String?, why: String?) -> WhyChip? {
        let l: String? = UpNextRules.nonBlank(label)
        let w: String? = UpNextRules.nonBlank(why)
        if l == nil && w == nil { return nil }
        return WhyChip(text: l ?? noLabel, why: w)
    }

    /// The line under the row: the server's sentence exactly, only while open.
    public static func line(_ chip: WhyChip?, open: Bool) -> String? {
        open ? chip?.why : nil
    }

    /// A room queue entry's chip: only an Up next pick has one.
    public static func room(_ item: QueueItem) -> WhyChip? {
        item.isPick ? of(label: item.reasonLabel, why: item.why) : nil
    }

    /// A phone queue row's chip: only an Up next pick has one.
    public static func phone(_ item: MusicPlayerItem) -> WhyChip? {
        item.queueOrigin == .auto ? of(label: item.reasonLabel, why: item.why) : nil
    }
}
