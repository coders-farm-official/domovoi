import Foundation
import Observation
import Synchronization

/// Settings → Phones & browsers: the three cards this batch owns, as
/// decisions and words.
///
/// A port of the first three `PanelCard`s of
/// `android/.../ui/screens/settings/ConnectionPanel.kt` (map §6.1-§6.4):
/// **Server**, **Household token** and **Trusted servers**, plus the
/// forgetting cascade they both reach.
///
/// ### §6.5, and which of its cards is now here
///
/// **Listening as** is here, as of the 2026-11-03 batch: The owner's answer 11,
/// "yes, match Android: build the 'listening as' picker". Its store is
/// ``PlayerListenerStore`` and its rows are ``listenerOptions(people:)``.
///
/// The rest of §6.5 is still not here, and still not stubbed: **Appearance**
/// (a theme this app has no store for and no asset catalogue to switch),
/// **This device** (a name read back from the device register) and **This
/// phone belongs to** (which writes an owner the device register sends, not a
/// listener). A theme chip that does not change the theme is the misleading
/// control the owner has ruled out twice.

// MARK: - §6.3, the rows

/// One row of the trusted-servers list.
public struct TrustedServerRow: Equatable, Sendable, Identifiable {
    public let url: String
    public let name: String?
    public let active: Bool

    public var id: String { url }
    public var address: String { ServerCredentials.address(url) }
    public var displayName: String { name ?? ShellStrings.unnamedServer }

    public init(url: String, name: String?, active: Bool) {
        self.url = url
        self.name = name
        self.active = active
    }

    /// The active row asks first; any other row forgets on the tap. The
    /// asymmetry is Android's — see ``ForgetAffordance``.
    public var forgetAffordance: ForgetAffordance {
        active ? .confirmFirst : .immediate
    }
}

/// What Settings → Phones & browsers lists: **the union** of the trust list,
/// the known-server keys and the active URL, active first and then by
/// address.
///
/// `ConnectionPanel.kt:544`. The active server is listed even when nothing
/// else knows it, "so there is never a server the phone talks to that the
/// list does not show"; the two lists can drift (a pre-trust-list install, a
/// harness write) and the union covers that.
public func trustedServersRows(
    trusted: Set<String>,
    known: [KnownServer],
    active: String
) -> [TrustedServerRow] {
    let activeClean = ServerCredentials.normalize(active)
    var names: [String: String?] = [:]
    for server in known { names[ServerCredentials.normalize(server.url)] = server.name }

    var urls = Set(trusted.map(ServerCredentials.normalize))
    urls.formUnion(names.keys)
    urls.insert(activeClean)
    urls = urls.filter { !ServerCredentials.isBlank($0) }

    return urls
        .map { TrustedServerRow(url: $0, name: names[$0] ?? nil, active: $0 == activeClean) }
        .sorted { left, right in
            if left.active != right.active { return left.active }
            return left.address < right.address
        }
}

// MARK: - §4.4 and §6.1, what the phone holds the active server to

/// The four-way identity display for the active server, which is where
/// "trusted" and "pinned" are visibly **different things**.
///
/// `IdentitySection` (`ConnectionPanel.kt:405`). Trust is per saved spelling
/// and lives in the trust list; a pin is per `scheme://host:port` and is what
/// the server must prove before the token goes out. A trusted server with no
/// pin is a normal, visible state — and the one case that must never happen
/// silently is the third: a server trusted while it offered no identity,
/// which has grown one since, is **never pinned behind the person's back**.
public enum IdentityDisplay: Equatable, Sendable {

    /// A key is pinned. The token goes out only after the server proves it.
    case pinned(fingerprint: String)

    /// Nothing is pinned, and the server proves a key it has not been held
    /// to. The fingerprint, and a **pin this identity** button.
    case offered(ServerIdentity.Pin)

    /// The server offers no identity at all (an older web backend), so the
    /// token is sent as before, unverified.
    case noIdentityOffered

    /// Nothing proved yet on this network.
    case notPinnedYet

    /// Which of the four, from the pin and the verdict.
    ///
    /// The order is the Kotlin's `when`, and the first branch is why: a
    /// pinned key wins over an offered one, so a `legacy(offered:)` verdict
    /// for a server that *is* pinned (which the gate does not produce, but
    /// which a stale status could) cannot draw a button that would overwrite
    /// the pin.
    public static func of(pin: ServerIdentity.Pin?, verdict: IdentityVerdict?) -> IdentityDisplay {
        if let pin { return .pinned(fingerprint: pin.fingerprint) }
        if case .legacy(let offered)? = verdict, let offered { return .offered(offered) }
        if case .legacy? = verdict { return .noIdentityOffered }
        return .notPinnedYet
    }

    /// The fingerprint drawn in monospace above the paragraph, or nil.
    public var fingerprint: String? {
        switch self {
        case .pinned(let fingerprint): return fingerprint
        case .offered(let pin): return pin.fingerprint
        case .noIdentityOffered, .notPinnedYet: return nil
        }
    }

    /// Whether the **pin this identity** button is drawn.
    public var offersPinning: Bool {
        if case .offered = self { return true }
        return false
    }

    /// The paragraph under it, verbatim from `ConnectionPanel.kt:412-452`.
    public var detail: String {
        switch self {
        case .pinned:
            return "Compare with Settings → About & updates on the dashboard. The household token is "
                + "sent only after the server proves this identity: on every network, "
                + "every ten minutes, and whenever the app comes back to the foreground."
        case .offered:
            return "Not pinned: this server was trusted while it offered no identity, and it "
                + "proves this one now. Compare it with Settings → About & updates on the dashboard, "
                + "then pin it — from then on the household token goes out only after a "
                + "proof of this key. Until then it is sent as before (unverified)."
        case .noIdentityOffered:
            return "none — this server offers no identity (an older web backend), so the household "
                + "token is sent as before, unverified: the app cannot tell this server from "
                + "another one at the same address."
        case .notPinnedYet:
            return "not pinned yet — the identity the trust dialog showed is pinned when a server is "
                + "trusted; a server trusted before this build is pinned the first time it proves one"
        }
    }
}

// MARK: - §5.1, whether the pairing screen may offer the field at all

/// Whether a server may be offered the household-token field.
///
/// **Finding A6-03: a 401 may invite a paste only from a server that has
/// proved itself.** The refusal that raises the pairing screen is already
/// filtered by the gate — ``AppContainer/shellInputs()`` reads
/// `pairingRequired`, which the client sets only for the active origin — and
/// ``DomovoiClient/checkDeviceToken(_:)`` refuses to send a candidate the
/// gate does not admit. This is the third place, the screen's own: a verdict
/// that does not admit the token does not get a field either, so the person
/// is never asked to paste a household token for a server that has not shown
/// it is theirs.
///
/// A **nil** verdict answers true, and that is not an oversight: it is the
/// state of a server that has not been asked yet, which is every server on a
/// cold launch, and it is the state Android is permanently in (it has no such
/// check at all). Withholding the field there would make the pairing screen
/// unusable for the ordinary case. What it rules out is the case with a
/// verdict that says no.
public func pairingOffersTheField(verdict: IdentityVerdict?) -> Bool {
    guard let verdict else { return true }
    return verdict.admitsToken
}

/// What the pairing screen says instead of offering a field, when
/// ``pairingOffersTheField(verdict:)`` says no — **per verdict**, because the
/// three refusing verdicts are three different facts about the server. Nil
/// for the verdicts that get a field.
///
/// ### Why this is not one sentence for all of them
///
/// It was, and the one it borrowed was the local shell's impostor banner
/// (``identityChangedText(address:canTrustAgain:)``), whose comment called it
/// "the one thing that can honestly be said". It is honest about a
/// `.mismatch` — and a `.mismatch` is the one verdict that **cannot reach
/// this screen**: ``shellMode(serverURL:unreachable:pairingRequired:preferLocal:identityChanged:tokenTrouble:)``
/// asks `identityChanged` before `pairingRequired`, so a mismatch returns
/// `.local` and gets the banner there. What arrives here is `.unproven` and
/// `.unavailable` — and `.unavailable` is the verdict
/// ``ServerChoice/coreDown(label:)`` documents as *"not an impostor, never a
/// reason to forget it"*: a box that is restarting, updating or busy.
///
/// So the screen told a person whose Domovoi was restarting that it had
/// answered "not with the identity this phone trusted", and pointed them at
/// the forgetting cascade — which drops the household token and the pin. They
/// would forget a healthy server and pair again from scratch. Two smaller
/// untruths rode along: this screen is not "showing its own media", and
/// neither the server list nor Settings is reachable from it (it replaces the
/// whole shell; `not now` is the only control).
///
/// ### What each verdict gets
///
///  - `.unproven` and `.unavailable` → ``TokenPasteField/message(_:)``'s
///    `.unavailable` sentence, which is **the answer the field itself would
///    have given**: ``DomovoiClient/checkDeviceToken(_:)`` returns
///    `.unavailable` for every verdict the gate refuses, and that string's own
///    doc says it covers "a 503, no answer, **or a server that did not prove
///    itself**. NOT 'that token is wrong'". No forget advice, no claim about
///    identity that was not proved, and nothing invented.
///  - `.mismatch` → the impostor words, which are true of it. **This branch
///    is unreachable through `shellMode` today**, for the reason above; it is
///    not dead code to delete, because it is the only honest answer if a
///    later change lets a mismatch reach the pairing screen, and because
///    `pairingOffersTheField` is a public rule anything may ask.
///  - `.verified`, `.legacy`, nil → nil: those get a field.
///
/// The `.unproven` case is the one the owner may want a fuller sentence for
/// ("this server has not proved it is the Domovoi this phone trusted", with
/// no forgetting advice). Writing one here would be inventing a user-facing
/// string, so this uses the closest honest existing one and the report asks.
public func pairingWithheldText(
    address: String,
    verdict: IdentityVerdict?,
    canTrustAgain: Bool
) -> String? {
    switch verdict {
    case .verified, .legacy, nil:
        return nil
    case .mismatch:
        return identityChangedText(address: address, canTrustAgain: canTrustAgain)
    case .unproven, .unavailable:
        // Never nil for `.unavailable`; `PhonesAndBrowsersTests` pins that,
        // because a nil here would leave the screen with neither a field nor
        // a word.
        return TokenPasteField.message(.unavailable)
    }
}


// ===========================================================================
// §6.5 "Listening as" — whose songs and resume positions this phone's are
// ===========================================================================

/// This phone's record of who is listening, **per server** (2026-11-12).
///
/// `Prefs.listenerPicks` + `Prefs.listenerPersonId` (`Prefs.kt`, Android
/// a1e611b6): the stored thing is the *pick* for each server — a person id,
/// ``ListenerPicks/household``, or nothing (follow whose the phone is) — and
/// the listener the app acts as, ``personID``, is worked out from the pick,
/// whose the phone is there and whether it is a shared screen
/// (``ListenerPicks/effective(_:owner:shared:)``). It was one value for the
/// install until then; a person id belongs to the server that issued it, and
/// "follow the phone" can only mean something per server.
///
/// It is a `String?` and not an `Int?` for the same reason Android's is: the
/// stored value came from a text field once and may be anything, and the one
/// place that matters — the query and the body — parses it as an integer and
/// **leaves it out when it is not one** (``PlayerServerPositions/readQuery(deviceID:personID:)``).
///
/// **Which server is "here"** is the shell's to say: ``attach(book:server:)``
/// hands over the owner book and the active address. A store nobody has
/// attached (a test's) keeps its picks under the empty address, and the
/// first attach moves them to the server the phone is on — the same move a
/// pick saved before 2026-11-12, when this was one value, gets (`Prefs.kt`:
/// "the pick from before it was per server belongs to the server the phone
/// was on").
///
/// Over ``SavedState`` rather than `UserDefaults` directly, so a test can
/// drive it with a ``SavedStateBox``.
public final class PlayerListenerStore: @unchecked Sendable {

    /// Its own `UserDefaults` key, under the app's prefix. **Not a field in
    /// the shell's blob**: the shell's blob is one write of the server, the
    /// trust and the pins, and picking a listener has nothing to do with any
    /// of them (``UserDefaultsSavedState/chatPinsKey`` says the same about
    /// chat pins).
    public static let key = "domovoi.player.listener"

    private struct State {
        var picks: [String: String]
        var book: (any ClaimBook)?
        var server: (@Sendable () -> String)?
    }

    /// The saved shape since 2026-11-12. The shape before it was the bare
    /// id as text, which ``decode(_:)`` still reads.
    private struct Saved: Codable {
        var picks: [String: String]
    }

    private let place: any SavedState
    private let held: Mutex<State>

    public init(place: (any SavedState)? = nil) {
        self.place = place ?? UserDefaultsSavedState(key: PlayerListenerStore.key)
        held = Mutex(State(picks: Self.decode(self.place.read())))
    }

    /// The shell's wiring: whose the phone is per server, and the active
    /// address, read at each question. Picks made before (under no server)
    /// move to the active server unless it already has one of its own.
    public func attach(book: any ClaimBook, server: @Sendable @escaping () -> String) {
        let active = ServerCredentials.normalize(server())
        let moved: [String: String]? = held.withLock { s in
            s.book = book
            s.server = server
            guard let loose = s.picks[""] else { return nil }
            s.picks[""] = nil
            if !active.isEmpty, s.picks[active] == nil { s.picks[active] = loose }
            return s.picks
        }
        if let moved { write(moved) }
    }

    private struct Here {
        let server: String
        let pick: String?
        let owner: DeviceOwner?
        let shared: Bool
    }

    private func here() -> Here {
        let (book, server, picks) = held.withLock { ($0.book, $0.server, $0.picks) }
        let active = ServerCredentials.normalize(server?() ?? "")
        return Here(
            server: active,
            pick: picks[active],
            owner: book?.deviceOwner(for: active),
            shared: book?.sharedScreen(for: active) == true
        )
    }

    /// Who the phone listens as on the active server, or nil for the
    /// household: the pick there, else whose the phone is (not on a shared
    /// screen). What every position request and listening-history row reads.
    public var personID: String? {
        let h = here()
        return ListenerPicks.effective(h.pick, owner: h.owner?.id, shared: h.shared)
    }

    /// Whose the phone is on the active server, as it last said; nil on a
    /// shared screen, where it is nobody's.
    public var ownerHere: DeviceOwner? {
        let h = here()
        return h.shared ? nil : h.owner
    }

    /// Whether the active server last said this phone is a shared screen.
    public var sharedHere: Bool { here().shared }

    /// The raw pick for `server`: a person id, ``ListenerPicks/household``, or
    /// nil (follow the phone). What a listening-history row is sent with.
    public func pick(for server: String) -> String? {
        held.withLock { $0.picks[ServerCredentials.normalize(server)] }
    }

    /// Pick who the phone listens as on the active server: a person id, or
    /// nil for the household. A blank is the household, so a stored empty
    /// string from any source never reads as a person called nothing. Picking
    /// whose the phone is goes back to following it
    /// (``ListenerPicks/toStore(_:owner:shared:)``).
    public func set(_ id: String?) {
        let tidy = id?.trimmingCharacters(in: .whitespacesAndNewlines)
        let chosen = (tidy?.isEmpty ?? true) ? nil : tidy
        let h = here()
        let stored = ListenerPicks.toStore(chosen, owner: h.owner?.id, shared: h.shared)
        let next: [String: String] = held.withLock { s in
            s.picks[h.server] = stored
            return s.picks
        }
        write(next)
    }

    /// A server marked this phone a shared screen: its pick there goes (the
    /// web's `SpokenAudio.forgetPick`).
    public func forgetPick(for server: String) {
        let key = ServerCredentials.normalize(server)
        let next: [String: String]? = held.withLock { s in
            guard s.picks[key] != nil else { return nil }
            s.picks[key] = nil
            return s.picks
        }
        if let next { write(next) }
    }

    private func write(_ picks: [String: String]) {
        place.write((try? JSONEncoder().encode(Saved(picks: picks))) ?? Data())
    }

    /// A blob that cannot be read is **no pick**, not a crash: losing the
    /// choice costs one trip to Settings, where a throw here would be a
    /// launch that fails on a corrupt plist. The shape from before
    /// 2026-11-12 — the bare id as text — is a pick under no server, which
    /// ``attach(book:server:)`` moves to the server the phone is on.
    private static func decode(_ blob: Data?) -> [String: String] {
        guard let blob, !blob.isEmpty else { return [:] }
        if let saved = try? JSONDecoder().decode(Saved.self, from: blob) { return saved.picks }
        guard let text = String(data: blob, encoding: .utf8)?
                  .trimmingCharacters(in: .whitespacesAndNewlines),
              !text.isEmpty
        else { return [:] }
        return ["": text]
    }
}

/// One row of the "Listening as" dropdown, and of "This phone belongs to":
/// `nil` first, then the household's people.
///
/// `listenerOptions = listOf(null) + people` (`ConnectionPanel.kt`). `name`
/// is the person's name, for a toast; `label` is what the row reads, which
/// for the phone's own person is "Alex (this phone)" in "Listening as".
public struct ListenerOption: Equatable, Sendable, Identifiable {

    /// Nil for the household (nobody in particular).
    public let personID: String?
    public let name: String
    public let label: String

    public var id: String { personID ?? "" }

    public init(personID: String?, name: String, label: String? = nil) {
        self.personID = personID
        self.name = name
        self.label = label ?? name
    }
}

/// The "Listening as" rows for a household: "household", then everyone,
/// with the phone's own person marked (`ConnectionPanel.kt`'s label:
/// `it.id == owner?.id && !sharedHere -> "${it.name} (this phone)"`).
///
/// A people list that has not answered yet is **just the nil row**, which is
/// honest: the picker still works, and "household" needs no server.
public func listenerOptions(people: [Person], ownerID: Int? = nil) -> [ListenerOption] {
    [ListenerOption(personID: nil, name: ShellStrings.listenerHousehold)]
        + people.map {
            ListenerOption(
                personID: String($0.id),
                name: $0.name,
                label: $0.id == ownerID ? ShellStrings.listenerThisPhone($0.name) : $0.name
            )
        }
}

/// Which option is selected for a listener id.
///
/// `people.firstOrNull { it.id.toString() == listenerId }`, and the **nil
/// row when the person is not in the list** — a person removed from the
/// household on the dashboard leaves this phone pointing at an id that no
/// longer names anybody, and "household" is the only honest thing to show.
public func selectedListener(_ options: [ListenerOption], personID: String?) -> ListenerOption? {
    guard let personID, !personID.isEmpty else { return options.first }
    return options.first { $0.personID == personID } ?? options.first
}

/// The household, for the "listening as" picker, "This phone belongs to"
/// and the two hint chips.
///
/// `rememberApi("people-hint") { it.api.get("/api/people").decode<List<PersonRow>>() }`
/// (`AudiobooksScreen.kt:339`, `PodcastsScreen.kt:308`, and the panel's own
/// in `ConnectionPanel.kt`). One read of `/api/people`, keyed to the server
/// like every other read in this app, so a house move re-asks and a name from
/// the old house cannot be shown under the new one's listener id.
///
/// It is a model rather than a `@State` in each of the three views for the
/// standing reason: a view that holds a `Task` is a view whose request dies
/// when a lazy row recycles, and the name under a chip is read on every
/// redraw.
@MainActor
@Observable
public final class PlayerListenerModel {

    public let people: ApiRead<[Person]>

    private let listeners: PlayerListenerStore

    /// Who this phone listens as here, or nil for the household.
    ///
    /// **A stored mirror of the store, and that is the point.**
    /// ``PlayerListenerStore`` is a plain `final class` over a `Mutex` — it
    /// has to be, because it is `Sendable` and read off the main actor by
    /// every position request — so a `var` that read straight through it
    /// would register no view against a change. Re-synced from the store in
    /// ``start()`` and in ``adopt()``, never written independently.
    public private(set) var personID: String?

    /// Whose the phone is here (nil: nobody's, or a shared screen), mirrored
    /// from the store with ``personID`` and for the same reason.
    public private(set) var owner: DeviceOwner?

    /// Whether the server last said this phone is a shared screen.
    public private(set) var shared = false

    /// A "This phone belongs to" pick on its way to the server.
    public private(set) var claiming = false

    public init(in container: AppContainer, listeners: PlayerListenerStore) {
        self.listeners = listeners
        self.personID = listeners.personID
        self.owner = listeners.ownerHere
        self.shared = listeners.sharedHere
        let client = container.client
        self.people = ApiRead(in: container) {
            try await client.get(PeoplePath.roster, as: [Person].self)
        }
    }

    /// Take whatever the store now holds: after a pick, after a claim, and
    /// on every appearance.
    public func adopt() {
        personID = listeners.personID
        owner = listeners.ownerHere
        shared = listeners.sharedHere
    }

    /// The screen appeared.
    ///
    /// Re-read only when there is nothing yet, or when the house has moved
    /// (``ApiRead/serverChanged()``, which re-reads only if the address is
    /// actually different).
    public func start() {
        adopt()
        if people.data == nil, people.error == nil {
            people.refresh()
        } else {
            people.serverChanged()
        }
    }

    public func stop() { people.stop() }

    /// The "Listening as" rows.
    public var options: [ListenerOption] {
        listenerOptions(people: people.data ?? [], ownerID: shared ? nil : owner?.id)
    }

    /// Which row is selected, which is "household" for a person who has since
    /// left the household.
    public var selected: ListenerOption? { selectedListener(options, personID: personID) }

    /// Whether a chip should be drawn at all.
    ///
    /// `val id = listenerId ?: return` (`AudiobooksScreen.kt:337`) — a phone
    /// listening as the household shows **nothing**, because that is the state
    /// every phone starts in and a chip for it would be on every screen for
    /// ever.
    public var showsChip: Bool { personID != nil }

    /// The chip's words, with an ellipsis until the name lands.
    public var chip: String {
        PlayerStrings.listeningAsChip(options.first { $0.personID == personID }?.name)
    }

    /// The toast after a pick (`ConnectionPanel.kt`): the plain name, never
    /// the "(this phone)" label.
    public func toast(for option: ListenerOption) -> String {
        option.personID == nil
            ? ShellStrings.listeningAsHousehold
            : ShellStrings.listeningAs(option.name)
    }

    /// The card's failure sentence, or nil while there is nothing to say.
    ///
    /// Only when the read has **no data at all**: `peopleState.error != null
    /// && peopleState.data == null` (`ConnectionPanel.kt`). A list that is a
    /// few seconds old with a failed refresh behind it is still a list worth
    /// picking from, which is the whole reason ``ApiRead`` keeps both.
    public var trouble: String? {
        guard people.data == nil, let why = people.error else { return nil }
        return PlayerStrings.couldNotLoadPeople(why)
    }

    // MARK: - "This phone belongs to"

    /// Its rows: "nobody in particular", then everyone (`ConnectionPanel.kt`:
    /// the same `listenerOptions`, labelled `it?.name ?: "nobody in particular"`).
    public var ownerOptions: [ListenerOption] {
        [ListenerOption(personID: nil, name: ShellStrings.nobodyInParticular)]
            + (people.data ?? []).map { ListenerOption(personID: String($0.id), name: $0.name) }
    }

    /// The row the SERVER's answer selects — never what was last asked for,
    /// so a refused claim never shows here. A person the roster does not
    /// have (yet) is still named, from the answer.
    public var selectedOwner: ListenerOption {
        guard let owner else { return ownerOptions[0] }
        return ownerOptions.first { $0.personID == String(owner.id) }
            ?? ListenerOption(personID: String(owner.id), name: owner.name)
    }

    /// Whether the card is drawn at all: only on a paired phone (an unpaired
    /// one would only be refused), as on Android.
    public static func showsOwnerCard(paired: Bool) -> Bool { paired }

    /// Pick whose this phone is, through the same PUT the People page uses,
    /// and say the SERVER's answer (``claimHere(_:personID:)``). One at a
    /// time: the picker is disabled while ``claiming``.
    public func pickOwner(_ option: ListenerOption, shell: ShellModel) {
        guard !claiming else { return }
        claiming = true
        let personID = option.personID.flatMap(Int.init)
        Task { @MainActor [weak self] in
            let out = await claimHere(shell, personID: personID)
            self?.claiming = false
            self?.adopt()
            shell.showToast(out.message)
        }
    }
}
