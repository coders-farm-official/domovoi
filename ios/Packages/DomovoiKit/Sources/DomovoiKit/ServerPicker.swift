import Foundation

/// What the server picker shows, as decisions rather than as drawing: which
/// rows, which pill, which forget, and — the one worth getting right — which
/// "nothing was found" line.
///
/// The pure half of `android/.../ui/shell/ServerPicker.kt` (map §3). The view
/// reads these and draws; it decides nothing, because nothing in the app
/// target is compiled by `ios/scripts/pkgtest.sh`.

// MARK: - §3.4, the three nothings

/// What the picker may say when the list is empty, which is **a statement of
/// fact about what the app actually did** and not a mood.
///
/// Four facts, and they are not interchangeable (`IOS-ONLY-STRINGS.md` §3):
/// 254 addresses were probed and none answered; the phone is not on a LAN so
/// nothing was probed; the phone is on a LAN with no address of its own so
/// nothing was probed; the Local Network permission was refused so nothing
/// could answer. Saying any one of them about any other of them is a control
/// claiming something it did not do, which the owner has ruled out by name
/// ([[no-misleading-controls]]).
///
/// ``nothing`` is the fifth state and the default: the panel has not swept
/// yet, so it has no fact to report and says nothing at all. Android's
/// condition is the same one (`scan.scanned`), reached differently.
public enum ServerPickerNotice: Equatable, Sendable {

    /// Nothing to say: no sweep has finished, or one found something.
    case nothing

    /// `total` addresses were probed on `port` and none answered.
    case nothingAnswered(port: Int)

    /// Not on Wi-Fi or Ethernet. **Nothing was probed.**
    case notOnWifi

    /// On a LAN, but with no site-local IPv4 of our own, so there is no /24
    /// to sweep. **Nothing was probed.**
    case noAddressYet

    /// The Local Network permission was refused. **Nothing could answer.**
    case localNetworkDenied

    /// The line the picker draws, or nil for ``nothing``.
    public var text: String? {
        switch self {
        case .nothing: return nil
        case .nothingAnswered(let port): return ShellStrings.nothingAnswered(port: port)
        case .notOnWifi: return ShellStrings.notOnWifi
        case .noAddressYet: return IOSOnlyStrings.noAddressYet
        case .localNetworkDenied: return IOSOnlyStrings.localNetworkDenied
        }
    }

    /// Whether this is the loud kind: the warn colour and the Wi-Fi-off
    /// glyph, which Android gives its off-LAN row and not its "nothing
    /// answered" line. A fact the person can act on is louder than one they
    /// cannot.
    public var isWarning: Bool {
        switch self {
        case .notOnWifi, .noAddressYet, .localNetworkDenied: return true
        case .nothing, .nothingAnswered: return false
        }
    }

    /// Whether this notice comes with a button that opens Settings.
    ///
    /// **False for every notice, including ``localNetworkDenied``.** The
    /// button is the owner's open question (``IOSOnlyStrings``); this is the
    /// seam, so the day the answer is yes the change is here and in one `if`,
    /// and so that a picker drawn today cannot grow a button nobody decided
    /// on.
    public var offersTheSettingsButton: Bool { false }
}

/// Which of the five ``ServerPickerNotice``s this panel is entitled to show.
///
/// **Three of the four facts are about the sweep and one is about the list**,
/// and that is why `rowsEmpty` gates only one branch. "Not on wifi", "no
/// address yet" and "local network access is off" are all true, and all worth
/// saying, with a saved server sitting in the list the person can still tap —
/// Android draws its own off-LAN line above the list for exactly that reason.
/// "Nothing answered on port 6369" is the only one that is a claim about the
/// list, and with a row in it nothing failed to be found.
///
/// Collapsing Android's two placements into one answer is deliberate: there,
/// an off-LAN rescan with an empty list draws the off-LAN line **and** the
/// "nothing answered" line, which contradict each other.
///
/// - Parameters:
///   - outcome: how the last sweep ended, or nil when none has finished —
///     the only thing that knows what was probed. Nil is "nothing has been
///     probed at all", and the panel then says nothing at all.
///   - scanning: whether one is running now. A sweep in flight has not
///     failed to find anything yet.
///   - rowsEmpty: whether the list the person is looking at is empty.
public func serverPickerNotice(
    outcome: SweepOutcome?,
    scanning: Bool,
    rowsEmpty: Bool,
    port: Int = Discovery.defaultPort
) -> ServerPickerNotice {
    guard !scanning, let outcome else { return .nothing }
    switch outcome {
    case .swept:
        // The one case where the addresses really were asked and really were
        // silent — and the only one the list's own emptiness decides.
        return rowsEmpty ? .nothingAnswered(port: port) : .nothing
    case .notOnLan:
        return .notOnWifi
    case .noLocalNetwork:
        return .noAddressYet
    case .localNetworkDenied:
        return .localNetworkDenied
    }
}

/// The section label above the list: the sweep's progress, "no domovois
/// found" or just "domovois".
///
/// Android's `when` (`ServerPicker.kt:177-183`) including its condition that
/// "no domovois found" needs `known` to be empty as well — a phone with a
/// saved server it cannot see has not found nothing.
public func serverPickerSectionLabel(
    progress: ServerScanProgress,
    swept: Bool,
    foundEmpty: Bool,
    knownEmpty: Bool
) -> String {
    if progress.running { return ShellStrings.scanning(done: progress.done, total: progress.total) }
    if swept, foundEmpty, knownEmpty { return ShellStrings.noDomovoisFound }
    return ShellStrings.domovois
}

// MARK: - §3.3, the rows

/// What the active row's pill says, or that the row offers `use` instead.
public enum ServerRowAffordance: Equatable, Sendable {
    /// The active server, in use: `connected`, pulsing.
    case connected
    /// The active server, not in use — the person chose this phone, or the
    /// server cannot be used: `selected`, idle.
    case selected
    /// Any other row: the word `use`.
    case use

    public var text: String {
        switch self {
        case .connected: return ShellStrings.connected
        case .selected: return ShellStrings.selected
        case .use: return ShellStrings.use
        }
    }
}

/// What the row's forget X does, which is **not the same for every row**.
///
/// The asymmetry is Android's and it is deliberate, pinned there by
/// `TrustedServersCardTest`: the active server's forget confirms first, any
/// other row's forgets at once. It is also `ANDROID-SHELL-MAP.md` §11.8 —
/// one stray tap on a 26 dp target unpairs a server silently, against
/// `ForgetActiveServerDialog`'s own argument that forgetting is
/// consequential. **Ported as it is** and raised in the report; a picker that
/// asked on both would be this batch re-deciding it.
public enum ForgetAffordance: Equatable, Sendable {
    /// No forget on this row: a server found by this sweep and never saved
    /// has nothing to forget.
    case none
    /// Forgets on the tap, with no confirmation.
    case immediate
    /// Raises ``ForgetActiveServerAsk``.
    case confirmFirst
}

/// One row of the picker's list.
public struct ServerPickerRow: Equatable, Sendable, Identifiable {
    public let url: String
    /// The `bot_name` the server offered, or nil — drawn as
    /// ``ShellStrings/unnamedServer``.
    public let name: String?
    /// Whether this row is a saved (known) server rather than one this sweep
    /// found and nothing has kept.
    public let saved: Bool
    /// Whether this is the active server.
    public let active: Bool
    /// Whether this sweep saw it answer.
    public let online: Bool
    /// The identity it advertised in this sweep, which is what a trust
    /// decision taken now would pin. Nil for a saved row nothing answered at
    /// — and that is the point: a trust decision cannot pin a key nothing
    /// offered.
    public let advertised: ServerIdentity.Pin?

    public var id: String { url }

    public var displayName: String { name ?? ShellStrings.unnamedServer }

    /// The address with the scheme stripped, as every row shows it.
    public var address: String { ServerCredentials.address(url) }

    public init(
        url: String,
        name: String?,
        saved: Bool,
        active: Bool,
        online: Bool,
        advertised: ServerIdentity.Pin?
    ) {
        self.url = url
        self.name = name
        self.saved = saved
        self.active = active
        self.online = online
        self.advertised = advertised
    }

    /// `inUse` is `!preferLocal && choice.enabled` (`ServerPicker.kt:92`):
    /// the saved server is "connected" only while it is the one in use; on
    /// the phone, or with it out of reach, it is merely selected.
    public func affordance(inUse: Bool) -> ServerRowAffordance {
        guard active else { return .use }
        return inUse ? .connected : .selected
    }

    public var forgetAffordance: ForgetAffordance {
        if active { return .confirmFirst }
        return saved ? .immediate : .none
    }

    /// Whether tapping the row does anything: `clickable(enabled = !active)`.
    public var isSelectable: Bool { !active }
}

/// The list: known servers first **in saved order**, then this sweep's finds
/// that no row already covers.
///
/// `ServerPicker.kt:209`, which is the one place the two lists are joined, so
/// the de-duplication rule lives here rather than in a view's `ForEach`.
public func serverPickerRows(
    known: [KnownServer],
    found: [FoundDomovoi],
    active: String
) -> [ServerPickerRow] {
    let activeClean = ServerCredentials.normalize(active)
    let online = Set(found.map(\.url))
    let advertised = Dictionary(found.map { ($0.url, $0.identity) }, uniquingKeysWith: { first, _ in first })
    var rows = known.map { server in
        ServerPickerRow(
            url: server.url,
            name: server.name,
            saved: true,
            active: server.url == activeClean,
            online: online.contains(server.url),
            advertised: advertised[server.url] ?? nil
        )
    }
    let knownURLs = Set(known.map(\.url))
    rows += found.filter { !knownURLs.contains($0.url) }.map { hit in
        ServerPickerRow(
            url: hit.url,
            name: hit.name,
            saved: false,
            active: hit.url == activeClean,
            online: true,
            advertised: hit.identity
        )
    }
    return rows
}

// MARK: - §3.6, the phone/server switch

/// Which side of the switch is lit.
///
/// `val onPhone = preferLocal || serverUrl.isBlank() || !choice.enabled`
/// (`ServerPicker.kt:421`). The server card is **greyed with the reason
/// visible rather than hidden**, which is why this takes the choice and not a
/// Bool.
public func connectionShowsThePhone(preferLocal: Bool, serverURL: String, choice: ServerChoice) -> Bool {
    preferLocal || ServerCredentials.isBlank(serverURL) || !choice.isEnabled
}

// MARK: - §3.5, a typed address

/// What happened to an address a person typed.
public enum ManualAddOutcome: Equatable, Sendable {
    /// Blank: silently nothing, as Android's `null` branch is.
    case ignored
    /// The address itself is refused — a plain-http address outside the home
    /// network, say — **before anything is probed**, with the policy's own
    /// reason rather than a later "couldn't reach".
    case refused(message: String)
    /// Nothing answered at the normalised URL.
    case noAnswer(message: String)
    /// A dashboard answered: straight into the trust gate, carrying the
    /// identity the probe saw **at that moment**, which is what a yes pins.
    case found(FoundDomovoi)
}
