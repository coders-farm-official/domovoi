import Foundation
import Observation

/// People → a person: "this phone" and "listening history" (2026-11-12),
/// the dashboard's PersonThisBrowser and PersonListening with "phone" for
/// "browser". The iOS half of `android/.../ui/screens/people/ThisPhone.kt`
/// (a1e611b6); words and rules are ``ListeningStrings``,
/// ``thisPhoneBlock(personID:name:owner:shared:keyHeld:deviceCount:)`` and
/// ``clearCheck(personID:name:owner:shared:keyHeld:nowMs:)``.

/// What a "this is me" / "not me" press came to. `refusal` is set when the
/// server said no; `message` is what to show either way — the SERVER's
/// answer, never what was asked for. `ClaimOutcome` (`ThisPhone.kt`).
public struct ClaimOutcome: Equatable, Sendable {
    public let ok: Bool
    public let message: String
    public let refusal: ClaimRefusal?

    public init(ok: Bool, message: String, refusal: ClaimRefusal? = nil) {
        self.ok = ok
        self.message = message
        self.refusal = refusal
    }
}

/// "This is me" (`personID`) or "not me" (nil) for this phone on the
/// active server, as ``ShellModel`` knows it. `claimHere` (`ThisPhone.kt`),
/// which the People page and Settings' "This phone belongs to" both call.
///
/// The server is read BEFORE the await and everything is keyed by it
/// (``claimThisPhone(client:book:server:deviceID:personID:)``). A refusal —
/// a 403 or a 409 above all — comes back as the server's own sentence, never
/// swallowed.
@MainActor
public func claimHere(_ shell: ShellModel, personID: Int?) async -> ClaimOutcome {
    let server = ServerCredentials.normalize(shell.container.store.serverURL)
    guard let deviceID = shell.deviceID, !server.isEmpty else {
        return ClaimOutcome(ok: false, message: failureText("claim", .noServer))
    }
    do {
        let owner = try await claimThisPhone(
            client: shell.container.client, book: shell.owners, server: server,
            deviceID: deviceID, personID: personID
        )
        shell.ownersChanged()
        return ClaimOutcome(
            ok: true,
            message: owner.map { ShellStrings.thisPhoneIsNow($0.name) } ?? ShellStrings.thisPhoneIsNobodysNow
        )
    } catch {
        shell.ownersChanged()
        let detail = claimDetail(error)
        return ClaimOutcome(
            ok: false,
            message: detail ?? failureText("claim", error),
            refusal: error.failure.map { claimRefusal(status: $0.status, detail: detail) }
        )
    }
}

/// The two sections a person's profile draws: "this phone" and "listening
/// history". One per detail, pointed at whoever is open (``show(_:)``), like
/// ``PersonDetailModel``, and keyed the same way: every answer is checked
/// against the person AND the server it was asked for before it is shown.
@MainActor
@Observable
public final class PeopleThisPhoneModel {

    private let shell: ShellModel

    /// `GET /api/music/listens/summary`, keyed to the server.
    public let summary: ApiRead<ListensSummary>

    /// Who the sections are about. Nil before ``show(_:)``.
    public private(set) var personID: Int?

    /// A claim on its way (both buttons are disabled).
    public private(set) var busy = false

    /// The last refused claim: the server's sentence, and what it offers.
    public private(set) var refused: ClaimOutcome?

    /// What a press of "clear" came to; nil while nothing is asked.
    public private(set) var ask: ClearCheck?

    /// A refused DELETE: the server's sentence.
    public private(set) var clearRefused: String?

    public private(set) var clearing = false

    private var generation = 0

    public init(shell: ShellModel) {
        self.shell = shell
        let client = shell.container.client
        self.summary = ApiRead(in: shell.container) {
            try await client.get(ListensSummary.path, as: ListensSummary.self)
        }
    }

    /// Point the sections at `person`. Re-showing the same person keeps what
    /// is there; another person starts clean.
    public func show(_ person: Person) {
        if personID != person.id {
            generation += 1
            personID = person.id
            busy = false
            refused = nil
            ask = nil
            clearRefused = nil
            clearing = false
        }
        if summary.data == nil, summary.error == nil { summary.refresh() } else { summary.serverChanged() }
    }

    public func stop() { summary.stop() }

    // MARK: - what is drawn

    private var server: String { ServerCredentials.normalize(shell.container.store.serverURL) }

    /// Whose this phone is here, as the server last said. Reads
    /// ``ShellModel/ownerRevision`` so a view drawing it follows a claim.
    public var owner: DeviceOwner? {
        _ = shell.ownerRevision
        return shell.owners.deviceOwner(for: server)
    }

    public var shared: Bool {
        _ = shell.ownerRevision
        return shell.owners.sharedScreen(for: server) == true
    }

    public var keyHeld: Bool {
        _ = shell.ownerRevision
        return shell.owners.holdsClaimKey(for: server)
    }

    /// Unpaired, a claim would only be refused: no button (no misleading
    /// controls), as on Android.
    public var paired: Bool { shell.container.client.deviceToken != nil }

    public func block(for person: Person) -> ThisPhoneBlock {
        thisPhoneBlock(
            personID: person.id, name: person.name, owner: owner, shared: shared,
            keyHeld: keyHeld, deviceCount: person.deviceCount
        )
    }

    /// The listening-history line.
    public func line(for person: Person) -> String {
        if let data = summary.data { return ListeningStrings.line(data, personID: person.id, name: person.name) }
        if let why = summary.error { return ListeningStrings.couldNotRead(why) }
        return ListeningStrings.reading
    }

    public func count(for person: Person) -> Int { summary.data?.count(for: person.id) ?? 0 }

    /// Whether "clear" is drawn: paired, and something is kept for them.
    public func showsClear(for person: Person) -> Bool {
        guard paired, let data = summary.data, !data.off else { return false }
        return data.count(for: person.id) > 0
    }

    /// The dashboard's People settings on the server this phone talks to.
    public var dashboardURL: URL? {
        systemSettingsDashboardURL(serverURL: shell.container.store.serverURL, section: "people")
            .flatMap(URL.init(string:))
    }

    // MARK: - "this is me" / "not me"

    /// `press` (`ThisPhone.kt`): the claim, the server's answer as a toast,
    /// or its refusal under the block. A person who has gone, or who another
    /// phone has, refreshes the roster.
    public func press(_ person: Person, claim: Bool, onPeopleChanged: @escaping @MainActor () -> Void = {}) {
        guard !busy else { return }
        busy = true
        refused = nil
        let mine = generation
        Task { @MainActor [weak self] in
            guard let self else { return }
            let out = await claimHere(self.shell, personID: claim ? person.id : nil)
            guard self.generation == mine else { return }
            self.busy = false
            if out.ok { self.shell.showToast(out.message) } else { self.refused = out }
            if out.ok || out.refusal == .gone || out.refusal == .takenByAnother { onPeopleChanged() }
        }
    }

    /// Whether the refusal under the block offers the dashboard: an admin's
    /// yes (another phone has them, or this phone can't prove whose it is).
    public var refusalOffersDashboard: Bool {
        refused?.refusal == .takenByAnother || refused?.refusal == .cantProve
    }

    // MARK: - clearing

    /// "clear" pressed: may this phone do it?
    public func askToClear(_ person: Person, nowMs: Int64 = Int64(Date().timeIntervalSince1970 * 1000)) {
        clearRefused = nil
        ask = clearCheck(
            personID: person.id, name: person.name, owner: owner, shared: shared,
            keyHeld: keyHeld, nowMs: nowMs
        )
    }

    public func dismissAsk() { ask = nil }

    /// The confirm dialog's "Clear": the DELETE, as this phone's person, at
    /// the server read now — and only after ``askToClear(_:nowMs:)`` said
    /// ``ClearCheck/allowed``.
    public func confirmClear(_ person: Person) {
        guard ask == .allowed, !clearing, let deviceID = shell.deviceID else { return }
        clearing = true
        let mine = generation
        let at = server
        let client = shell.container.client
        let book = shell.owners
        Task { @MainActor [weak self] in
            do {
                let n = try await clearListeningHistory(
                    client: client, book: book, server: at, deviceID: deviceID, personID: person.id
                )
                guard let self, self.generation == mine else { return }
                self.shell.showToast(ListeningStrings.cleared(person.name, n))
                self.summary.refresh()
            } catch {
                guard let self, self.generation == mine else { return }
                // A 403 says why in its own words.
                self.clearRefused = claimDetail(error) ?? failureText("clear", error)
            }
            guard let self, self.generation == mine else { return }
            self.clearing = false
            self.ask = nil
        }
    }

    /// The not-here dialog's "this is me": the claim, its answer as a toast.
    public func thisIsMeFromClear(_ person: Person) {
        ask = nil
        let shell = self.shell
        Task { @MainActor in
            let out = await claimHere(shell, personID: person.id)
            shell.showToast(out.message)
        }
    }
}
