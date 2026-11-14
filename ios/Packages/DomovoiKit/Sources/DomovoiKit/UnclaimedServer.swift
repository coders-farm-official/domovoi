import Foundation

// A Domovoi nobody has claimed yet — a port of
// `android/.../ui/shell/UnclaimedServer.kt` (commits 70ce74be and cfe9b0dd,
// owner 2026-11-04) and of the status read in `AppShell.kt:165-197` that
// drives it.
//
// Before first-run admin setup the server keeps a LAN grace: every read the
// phone makes works, so the app used to show a whole workspace for a house
// with no admin and nothing saying "start here". The claim happens on the
// dashboard, so the app shows ONE screen in place of the workspace instead —
// what to do, `Check again`, and the ways out: open the dashboard, the
// phone's own media (the local shell), another server.
//
// What says "unclaimed" is the open `GET /api/auth/status` answering
// `setup_complete: false`. Anything else — true, missing, unreadable, a
// failed read, an older server — is NOT unclaimed: a doubtful read never
// blocks the house.

/// Every word the screen says, copied from `UnclaimedServer.kt`.
public enum UnclaimedServerStrings {

    /// `UNCLAIMED_TITLE` (`UnclaimedServer.kt:63`).
    public static let title = "This Domovoi isn't set up yet"

    /// What VoiceOver says for the address line under the title — **not**
    /// what is drawn, which stays the bare address so it can be typed into a
    /// browser character for character.
    ///
    /// Train I014: Apple's audit failed the blocker with "Label not
    /// human-readable" against `127.0.0.1:6606`, the address as a line of
    /// text by itself. That is the third time the app has met this finding
    /// on a bare address, and the route is the one the first two took: the
    /// forget-server card (``forgetServerAddressLabel(_:)``, 2026-10-30) and
    /// the phone-source switch's server segment
    /// (``PhoneMediaStrings/serverSideLabel(_:)``, train S006) both keep the
    /// visible text and give VoiceOver `the server at <address>`. This is
    /// that wording, from the same function, so the three cannot drift.
    ///
    /// **iOS-only**: Android's `Text(address)` (`UnclaimedServer.kt:104-108`)
    /// has no content description and TalkBack reads it raw.
    public static func addressLabel(_ address: String) -> String {
        forgetServerAddressLabel(address)
    }

    /// `unclaimedText(address)` (`UnclaimedServer.kt:66-69`): the explanation,
    /// with the address to open.
    public static func text(address: String) -> String {
        "Open its dashboard in a web browser and claim it, then come back. "
            + "On a computer or phone on the same network, go to \(address): it asks for the setup code "
            + "from the computer Domovoi is installed on, and an admin password."
    }

    /// `UNCLAIMED_STILL` (`UnclaimedServer.kt:72`): what the read `Check
    /// again` started says when the house is still unclaimed.
    public static let still = "still not set up — claim it on the dashboard first"

    /// `UNCLAIMED_HINT` (`UnclaimedServer.kt:79-81`).
    public static let hint = "Not the one setting it up? Ask whoever installed Domovoi, or use this phone's own music, "
        + "videos and files until it's ready."

    /// The filled button (`UnclaimedServer.kt:112`).
    public static let checkAgain = "Check again"

    /// The filled button while a read is out (`UnclaimedServer.kt:112`).
    public static let checking = "checking…"

    /// The first text button (`UnclaimedServer.kt:118`).
    public static let openTheDashboard = "open the dashboard"

    /// The toast when nothing on the phone took the dashboard's address
    /// (`UnclaimedServer.kt:117`). Android's own words for this screen, which
    /// are not Settings' `no browser found to open the dashboard`.
    public static let noBrowser = "no browser on this phone"

    /// The second text button (`UnclaimedServer.kt:120`): the local shell.
    public static let useThisPhonesMedia = "use this phone's media"

    /// The third text button (`UnclaimedServer.kt:121`): the connection
    /// dialog.
    public static let useADifferentServer = "use a different server"
}

/// The open status route. No dependency on the server (`api/auth`), and read
/// **untracked**: Android reads it with `getUntracked` so that a refusal from
/// it can never raise or clear the pairing screen.
public let authStatusPath = "/api/auth/status"

/// `/api/auth/status`'s answer, as far as this screen cares.
///
/// `setup_complete` is read **leniently**: a field that is missing, `null` or
/// not a boolean is nil — "it does not say" — rather than a failed decode.
/// That is `setupCompleteOf`'s `booleanOrNull` (`UnclaimedServer.kt:55-56`).
/// A body that is not a JSON object at all still fails to decode, which the
/// read turns into nil as well.
public struct AuthStatusAnswer: Decodable, Equatable, Sendable {
    public let setupComplete: Bool?

    private enum CodingKeys: String, CodingKey {
        case setupComplete = "setup_complete"
    }

    public init(setupComplete: Bool?) {
        self.setupComplete = setupComplete
    }

    public init(from decoder: any Decoder) throws {
        let fields = try decoder.container(keyedBy: CodingKeys.self)
        setupComplete = try? fields.decodeIfPresent(Bool.self, forKey: .setupComplete)
    }
}

/// `setup_complete` out of a `/api/auth/status` body; nil when it does not
/// say. Android's `setupCompleteOf`. Pure.
public func setupCompleteOf(_ body: Data?) -> Bool? {
    guard let body else { return nil }
    return (try? JSONDecoder().decode(AuthStatusAnswer.self, from: body))?.setupComplete
}

/// Whether the active server gets the unclaimed screen: only a configured
/// server whose status said, in so many words, `setup_complete: false`.
/// Android's `showsUnclaimed`. Pure.
public func showsUnclaimed(serverURL: String, setupComplete: Bool?) -> Bool {
    !ServerCredentials.isBlank(serverURL) && setupComplete == false
}

/// Whether a status read says ``UnclaimedServerStrings/still``: only a read
/// `Check again` started, and only when it found the house still unclaimed.
/// The reads a resume or a socket change start are quiet. Android's
/// `saysStillUnclaimed` (cfe9b0dd). Pure.
public func saysStillUnclaimed(fromButton: Bool, read: Bool?) -> Bool {
    fromButton && read == false
}

/// Whether the status is worth asking at all: a server is saved, it is in
/// reach, and it has not already said it is claimed. Once claimed it is not
/// asked again for that server — the first line of Android's effect
/// (`AppShell.kt:178`). Pure.
public func unclaimedAsks(serverURL: String, unreachable: Bool, setupComplete: Bool?) -> Bool {
    !ServerCredentials.isBlank(serverURL) && !unreachable && setupComplete != true
}

/// Whether the unclaimed screen is drawn **in place of** this shell screen.
///
/// Android draws it first thing in `ShellContent` (`AppShell.kt:317-322`),
/// which is the whole `Workspace` side: before the token-trouble card, before
/// the pairing screen, before the workspace. The local shell is never
/// replaced — "use this phone's media" is how a person gets past it, and a
/// house out of reach has nothing to claim. Pure.
public func unclaimedReplaces(_ screen: ShellScreen, blocks: Bool) -> Bool {
    guard blocks else { return false }
    switch screen {
    case .localMedia:
        return false
    case .workspace, .pairing, .tokenTroubleCard:
        return true
    }
}

/// The dashboard's plain address, which is where its first-run welcome and
/// the claim form are: `serverUrl.trim().trimEnd('/') + "/"`
/// (`UnclaimedServer.kt:117`). Nil for no server or one that is not a URL.
/// Pure.
public func unclaimedDashboardURL(_ serverURL: String) -> URL? {
    var base = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
    while base.hasSuffix("/") { base.removeLast() }
    guard !base.isEmpty, base.contains("://") else { return nil }
    return URL(string: base + "/")
}

/// What started a status read. Only ``checkAgain`` may say "still not set
/// up"; the rest are Android's effect keys (`AppShell.kt:177`).
public enum UnclaimedAsk: Equatable, Sendable {
    /// A server was picked (the effect's `serverUrl` key).
    case serverPicked
    /// The live socket came up or went down (`connected`).
    case socket
    /// The reachability verdict came back to "in reach" (`unreachable`).
    case reachable
    /// The app came back on screen (`repeatOnLifecycle(STARTED)`): the owner
    /// claimed it in a browser and switched back.
    case foreground
    /// The person tapped `Check again` (`claimChecks`).
    case checkAgain
}

/// Whether the active server has been claimed, asked and remembered **per
/// server**.
///
/// Android keeps the answer in `remember(serverUrl)` state, so a switch
/// starts from "not known", and its `LaunchedEffect` cancels a read in flight
/// whenever a key changes. Neither of those comes for free here: a read is a
/// `Task`, it runs to its end, and what it brings back can be about a server
/// the phone has since left — [[read-after-await-wrong-key]], this track's
/// top defect class. So every read carries **the address it was asked of and
/// the switch it was asked in**, both captured before the await, and its
/// answer is written only if both still hold when it lands:
///
///  - the read is **sent to** the captured address, not to whatever the
///    client's store says when the request is built. Otherwise a store that
///    went A → B → A before any refresh caught up could send A's read to B
///    and file B's answer under A, with every check below passing.
///  - ``serverIs(_:)`` bumps `epoch` on a switch, so a read from before the
///    switch is dropped whole — it neither blocks nor unblocks the new
///    server, and it does not touch the new server's `checking`.
///  - the address is compared with the **store's** current address as well,
///    because ``ShellModel/refresh()`` (which calls ``serverIs(_:)``) runs a
///    hop after the picker writes the store, and a read can land in between.
///  - `seq` stands in for the cancellation, **only in part**: an older read
///    that lands after a newer one is dropped, so a `false` asked before the
///    owner claimed the house cannot come back after the `true` asked after.
///    But an older read that lands **first** is still applied (Android would
///    have cancelled it), and it holds `checking…` — and the button off —
///    until it lands. Both are about the right house; the cost is a stale
///    answer shown until the newer one arrives, and a button that waits.
///
/// "A read that fails keeps what was known": a nil answer changes nothing
/// but `checking`.
@MainActor
@Observable
public final class UnclaimedServerModel {

    /// The address everything below is about. Moved only by
    /// ``serverIs(_:)``.
    public private(set) var server: String

    /// What the status last said for ``server``; nil for "not known".
    public private(set) var setupComplete: Bool?

    /// Reads out for ``server``.
    private var inFlight = 0

    /// Bumped by every switch. A read from an earlier epoch is about a server
    /// this model has stopped describing.
    private var epoch = 0

    /// The last read started, and the newest one that has landed.
    private var started = 0
    private var landedSeq = 0

    /// The first read started after `Check again`, which is the read that
    /// may say "still not set up" (Android's `askedByButton`, which the
    /// effect's restart hands to the next read rather than losing).
    private var buttonSeq: Int?

    private let read: @Sendable (String) async -> Bool?
    private let activeServer: @MainActor () -> String

    /// Where ``UnclaimedServerStrings/still`` goes: the shell's toast.
    @ObservationIgnored public var say: (@MainActor (String) -> Void)?

    /// Status reads sent, for a test to count.
    @ObservationIgnored public private(set) var readsSent = 0

    /// - Parameters:
    ///   - server: the address the shell starts on.
    ///   - read: one status read **of the address it is handed**, nil when
    ///     it failed or did not say. ``read(using:server:)`` is the real one.
    ///   - activeServer: the store's current address, read when a read goes
    ///     out and again when it lands.
    public init(
        server: String,
        read: @Sendable @escaping (String) async -> Bool?,
        activeServer: @MainActor @escaping () -> String
    ) {
        self.server = server
        self.read = read
        self.activeServer = activeServer
    }

    /// The real read: the open status route of **`server`**, untracked. Any
    /// failure is nil — a refusal (whatever its body says), a page that is
    /// not JSON, nothing answering.
    ///
    /// The address is spelt out as an absolute URL, which ``DomovoiClient``
    /// sends as it is (``DomovoiClient/absolute(_:)``) under the same scope
    /// rule as every request: the token goes only if `server` is still the
    /// active server, and the route needs none.
    nonisolated public static func read(using client: DomovoiClient, server: String) async -> Bool? {
        var base = server.trimmingCharacters(in: .whitespacesAndNewlines)
        while base.hasSuffix("/") { base.removeLast() }
        guard base.contains("://") else { return nil }
        return (try? await client.getUntracked(base + authStatusPath, as: AuthStatusAnswer.self))?.setupComplete
    }

    /// Whether a read is out — `checking…` on the button, and the button off.
    public var checking: Bool { inFlight > 0 }

    /// Whether the active server gets the unclaimed screen.
    public var blocks: Bool { showsUnclaimed(serverURL: server, setupComplete: setupComplete) }

    /// The shell is on `url` now. A different address starts from nothing,
    /// as `remember(serverUrl)` does, and drops every read still out.
    /// Returns whether it was a switch.
    @discardableResult
    public func serverIs(_ url: String) -> Bool {
        guard url != server else { return false }
        server = url
        setupComplete = nil
        inFlight = 0
        epoch += 1
        started = 0
        landedSeq = 0
        buttonSeq = nil
        return true
    }

    /// `Check again`. Refused while a read is out, as the button is.
    @discardableResult
    public func checkAgain(unreachable: Bool) -> Task<Void, Never>? {
        guard !checking else { return nil }
        return ask(.checkAgain, unreachable: unreachable)
    }

    /// Ask the server, if it is worth asking (``unclaimedAsks(serverURL:unreachable:setupComplete:)``).
    ///
    /// The returned task is the read; the app drops it, a test awaits it.
    @discardableResult
    public func ask(_ reason: UnclaimedAsk, unreachable: Bool) -> Task<Void, Never>? {
        guard unclaimedAsks(serverURL: server, unreachable: unreachable, setupComplete: setupComplete) else {
            return nil
        }
        // The store has moved ahead of ``serverIs(_:)``: an answer about this
        // address would be dropped on landing anyway, and the refresh that is
        // on its way asks the new one.
        let asked = activeServer()
        guard asked == server else { return nil }
        started += 1
        let seq = started
        if reason == .checkAgain, buttonSeq == nil { buttonSeq = seq }
        inFlight += 1
        readsSent += 1
        let askedIn = epoch
        let read = self.read
        return Task { @MainActor [weak self] in
            let answer = await read(asked)
            self?.landed(answer, asked: asked, epoch: askedIn, seq: seq)
        }
    }

    private func landed(_ answer: Bool?, asked: String, epoch askedIn: Int, seq: Int) {
        // A switch since: this read is about a house the model has left, and
        // `inFlight` was reset with it.
        guard askedIn == epoch else { return }
        inFlight = max(0, inFlight - 1)
        // The store moved and the refresh has not caught up yet.
        guard asked == server, asked == activeServer() else { return }
        // A newer read has already landed: this one would have been cancelled.
        guard seq > landedSeq else { return }
        landedSeq = seq
        let fromButton = buttonSeq.map { seq >= $0 } ?? false
        if fromButton { buttonSeq = nil }
        if let answer { setupComplete = answer }
        if saysStillUnclaimed(fromButton: fromButton, read: answer) {
            say?(UnclaimedServerStrings.still)
        }
    }
}
