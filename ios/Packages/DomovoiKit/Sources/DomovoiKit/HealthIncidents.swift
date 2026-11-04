import Foundation

// ---------------------------------------------------------------------------
// The container guard, the phone's half — the pure part of
// `alerts/ContainerIncidents.kt` (479 lines, read at `898b3af2`), which is
// itself the phone's half of `docs/CONTAINER_GUARD.md`.
//
// The core watches the Docker containers Domovoi expects and records
// incidents of two classes (`domovoi/container_guard/model.py:18-33`):
//
//   failure   a container missing, stopped, restarting or unhealthy, or
//             Docker itself down. Resolves by itself; an admin may
//             acknowledge it to hide the banner.
//   tamper    a different image, an unknown container under Domovoi's names,
//             risky settings. Pauses plugin installs, updates and room
//             upgrades until an admin clears it after review.
//
// What the server serves, by tier, verified in its own code:
//
//   GET /api/containers/incidents   `Depends(require_device_read)` — a paired
//                                   phone (`web/backend/api/containers.py:88`):
//                                   `views.household_payload`, the open and
//                                   recently resolved incidents in plain
//                                   words, the banner and the freeze.
//   GET /api/containers/summary     **open**, no credential (`:80`):
//                                   `views.public_summary`, only whether
//                                   something needs an admin and its colour.
//
// Acknowledge and clear are the dashboard admin's; neither app offers them.
// Everything here is a value or a pure function, so `swift test` can cover
// the screen's decisions — the reads themselves are `HealthGuard.swift`'s.
//
// **Named `Health…` throughout** because Settings → Health is where this is
// drawn and NIGHT-RULES §2 asks for a name per item; `alerts/` ports the
// notification half of the same Kotlin file under `Alerts…`, and the two sets
// are deliberately separate so neither branch's merge can redeclare the
// other's.
// ---------------------------------------------------------------------------

/// `CLASS_TAMPER` (`ContainerIncidents.kt:45`).
public let healthClassTamper = "tamper"

/// `CLASS_FAILURE` (`:46`) — and what anything else reads as.
public let healthClassFailure = "failure"

// MARK: - one incident

/// One incident as the household tier sees it — `ContainerIncident`
/// (`ContainerIncidents.kt:50`), decoded from `incident_view`
/// (`domovoi/container_guard/views.py:84`).
///
/// **Every field but ``id`` may be missing**, which is the Kotlin's own rule,
/// so the decoder fills what it can derive and a row with no usable `id` is
/// dropped rather than failing the read (`parseIncident`'s `?: return null`).
///
/// ### The four keys this model does not declare, and why
///
/// `expected`, `found`, `acknowledged_by`, `cleared_by` and `clear_note` are
/// added to a row **only for an admin caller** (`views.py:107`). A phone holds
/// a household token, so they never arrive, and a model that declared them
/// would be describing an answer this app cannot receive. `can_acknowledge`
/// and `can_clear` are declared nowhere either: both are `false` for a
/// household caller at both hops (`containers.py:133`, `:146`), so there is no
/// control for them to enable — see ``HealthPanelStrings/adminClearsThese``,
/// which is what the panel says instead.
public struct HealthIncident: Equatable, Sendable, Identifiable, Decodable {

    public let id: Int64

    /// `"failure"` or `"tamper"`; **anything else reads as a failure**
    /// (`parseIncident`, `:134`). `class` is a Swift keyword, so the property
    /// is the Kotlin's own name for it.
    public let cls: String

    /// missing, stopped, restarting, unhealthy, oneshot_failed, daemon,
    /// image, unknown, config, outdated — ``healthKindWords(_:)`` has the
    /// words.
    public let kind: String?

    /// `"error"` (red) or `"warn"` (amber).
    public let severity: String?

    public let label: String?
    public let summary: String?
    public let owner: String?
    public let container: String?
    public let openedAt: String?
    public let lastSeenAt: String?
    public let resolvedAt: String?

    /// null, recovered, no_longer_expected, cleared.
    public let resolution: String?

    public let acknowledged: Bool
    public let acknowledgedAt: String?
    public let clearedAt: String?

    /// `open` in the Kotlin and in the JSON. Renamed because `open` reads as
    /// Swift's access modifier at a use site and `inc.open` would be the one
    /// word in this file a reader has to stop at.
    public let isOpen: Bool

    public let freezes: Bool

    /// `tamper` (`:74`).
    public var isTamper: Bool { cls == healthClassTamper }

    /// What the banner shows: an open tamper incident (only clearing hides
    /// it), or an open failure nobody has acknowledged (`:78`).
    public var bannerWorthy: Bool { isOpen && (isTamper || !acknowledged) }

    /// Red: tampering, or a required container's failure (`:81`).
    public var isRed: Bool { isTamper || severity == "error" }

    public init(
        id: Int64,
        cls: String = healthClassFailure,
        kind: String? = nil,
        severity: String? = nil,
        label: String? = nil,
        summary: String? = nil,
        owner: String? = nil,
        container: String? = nil,
        openedAt: String? = nil,
        lastSeenAt: String? = nil,
        resolvedAt: String? = nil,
        resolution: String? = nil,
        acknowledged: Bool = false,
        acknowledgedAt: String? = nil,
        clearedAt: String? = nil,
        isOpen: Bool = true,
        freezes: Bool = false
    ) {
        self.id = id
        self.cls = cls
        self.kind = kind
        self.severity = severity
        self.label = label
        self.summary = summary
        self.owner = owner
        self.container = container
        self.openedAt = openedAt
        self.lastSeenAt = lastSeenAt
        self.resolvedAt = resolvedAt
        self.resolution = resolution
        self.acknowledged = acknowledged
        self.acknowledgedAt = acknowledgedAt
        self.clearedAt = clearedAt
        self.isOpen = isOpen
        self.freezes = freezes
    }

    enum CodingKeys: String, CodingKey {
        case id
        case cls = "class"
        case kind
        case severity
        case label
        case summary
        case owner
        case container
        case openedAt = "opened_at"
        case lastSeenAt = "last_seen_at"
        case resolvedAt = "resolved_at"
        case resolution
        case acknowledged
        case acknowledgedAt = "acknowledged_at"
        case clearedAt = "cleared_at"
        case isOpen = "open"
        case freezes
    }

    /// `parseIncident` (`:129`), branch for branch — including the three
    /// derived defaults, which are what a server that dropped a field still
    /// shows correctly.
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = c.healthInt64(.id) else {
            // `?: return null`. A throw here is dropped by
            // ``HealthIncidentRow``, so the row goes and the others stay.
            throw DecodingError.dataCorrupted(
                DecodingError.Context(
                    codingPath: c.codingPath,
                    debugDescription: "an incident with no usable id"
                )
            )
        }
        self.id = id
        let resolvedAt = c.healthString(.resolvedAt)
        let cls = c.healthString(.cls) == healthClassTamper ? healthClassTamper : healthClassFailure
        let acknowledgedAt = c.healthString(.acknowledgedAt)
        let isOpen = c.healthBool(.isOpen) ?? (resolvedAt == nil)
        self.cls = cls
        self.kind = c.healthString(.kind)
        self.severity = c.healthString(.severity)
        self.label = c.healthString(.label)
        self.summary = c.healthString(.summary)
        self.owner = c.healthString(.owner)
        self.container = c.healthString(.container)
        self.openedAt = c.healthString(.openedAt)
        self.lastSeenAt = c.healthString(.lastSeenAt)
        self.resolvedAt = resolvedAt
        self.resolution = c.healthString(.resolution)
        self.acknowledged = c.healthBool(.acknowledged) ?? (acknowledgedAt != nil)
        self.acknowledgedAt = acknowledgedAt
        self.clearedAt = c.healthString(.clearedAt)
        self.isOpen = isOpen
        self.freezes = c.healthBool(.freezes) ?? (cls == healthClassTamper && isOpen)
    }
}

/// One row of the `incidents` array, **or nothing** — `mapNotNull {
/// parseIncident(it) }` (`:204`).
///
/// A wrapper rather than a lenient array decoder, because `JSONDecoder` has no
/// way to skip an element: one row that is not an object, or has no `id`,
/// would otherwise throw away the whole read. The server sends four or five
/// rows and losing all of them over one is the failure this exists to prevent.
struct HealthIncidentRow: Decodable {
    let incident: HealthIncident?

    init(from decoder: any Decoder) throws {
        incident = try? HealthIncident(from: decoder)
    }
}

// MARK: - the banner the server computed

/// The banner the server computed for every page — `IncidentBanner`
/// (`ContainerIncidents.kt:85`), from `views.py:41`.
public struct HealthIncidentBanner: Equatable, Sendable, Decodable {

    public let show: Bool

    /// `"tamper"`, `"error"`, `"warn"`; nil while ``show`` is false.
    ///
    /// **Note it is `error` here and `err` on the summary route**
    /// (`views.py:79`). One letter, two routes, and
    /// ``HealthIncidentSummary/tone`` is the other one.
    public let tone: String?

    public let text: String
    public let count: Int

    /// `incident_ids` — the ids the banner is about.
    public let incidentIDs: [Int64]

    public let frozen: Bool

    public init(
        show: Bool = false,
        tone: String? = nil,
        text: String = "",
        count: Int = 0,
        incidentIDs: [Int64] = [],
        frozen: Bool = false
    ) {
        self.show = show
        self.tone = tone
        self.text = text
        self.count = count
        self.incidentIDs = incidentIDs
        self.frozen = frozen
    }

    enum CodingKeys: String, CodingKey {
        case show, tone, text, count
        case incidentIDs = "incident_ids"
        case frozen
    }

    /// `parseBanner` (`:182`). **`count` falls back to the number of ids**
    /// (`:190`), which is what a server that sends the list and not the count
    /// means.
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let ids = (try? c.decodeIfPresent([Int64].self, forKey: .incidentIDs)) ?? nil
        incidentIDs = ids ?? []
        show = c.healthBool(.show) ?? false
        tone = c.healthString(.tone)
        text = c.healthString(.text) ?? ""
        count = c.healthInt64(.count).map { Int($0) } ?? incidentIDs.count
        frozen = c.healthBool(.frozen) ?? false
    }
}

// MARK: - the two answers

/// `GET /api/containers/incidents` — `IncidentsPayload`
/// (`ContainerIncidents.kt:96`), the server's `household_payload`
/// (`views.py:118`).
///
/// **Decoding fails when there is no `incidents` array**, and that is the
/// point: `parseIncidents` returns null for a body that is not this
/// (`:201`), which is how "the route answered something else" becomes a
/// failure the tier rule can act on rather than an empty list the screen
/// would draw as a healthy house.
public struct HealthIncidentsPayload: Equatable, Sendable, Decodable {

    /// Open first, newest first; then those resolved in the last 24 hours.
    ///
    /// **The server's order is the screen's order** —
    /// `store.recent_incidents` is
    /// `ORDER BY (resolved_at IS NULL) DESC, opened_at DESC, id DESC LIMIT 50`
    /// (`domovoi/container_guard/store.py:87`). Nothing here re-sorts it.
    public let incidents: [HealthIncident]

    public let banner: HealthIncidentBanner
    public let frozen: Bool
    public let attention: Bool

    public init(
        incidents: [HealthIncident] = [],
        banner: HealthIncidentBanner? = nil,
        frozen: Bool? = nil,
        attention: Bool? = nil
    ) {
        let banner = banner ?? deriveHealthBanner(incidents)
        self.incidents = incidents
        self.banner = banner
        self.frozen = frozen ?? banner.frozen
        self.attention = attention ?? banner.show
    }

    enum CodingKeys: String, CodingKey {
        case incidents, banner, frozen, attention
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // `o["incidents"] as? JsonArray ?: return null` — the one required key.
        let rows = try c.decode([HealthIncidentRow].self, forKey: .incidents)
        let incidents = rows.compactMap(\.incident)
        // `parseBanner(o["banner"]) ?: deriveBanner(incidents)` (`:205`).
        let banner = ((try? c.decodeIfPresent(HealthIncidentBanner.self, forKey: .banner)) ?? nil)
            ?? deriveHealthBanner(incidents)
        self.incidents = incidents
        self.banner = banner
        self.frozen = c.healthBool(.frozen) ?? banner.frozen
        self.attention = c.healthBool(.attention) ?? banner.show
    }

    /// `IncidentState.open` (`:257`) — in the server's order.
    public var openIncidents: [HealthIncident] { incidents.filter(\.isOpen) }

    /// `IncidentState.resolved` (`:258`) — `filterNot { it.open }`, so the
    /// last day's resolved and cleared ones, still in the server's order.
    public var resolvedIncidents: [HealthIncident] { incidents.filter { !$0.isOpen } }
}

/// `GET /api/containers/summary` — `IncidentSummary` (`:105`), the server's
/// `public_summary` (`views.py:74`). The open tier's whole answer.
public struct HealthIncidentSummary: Equatable, Sendable, Decodable {

    public let attention: Bool

    /// `"err"` or `"warn"` — **not** the banner's `"error"` (`views.py:79`);
    /// nil with nothing to show.
    public let tone: String?

    public let text: String

    public init(attention: Bool = false, tone: String? = nil, text: String = "") {
        self.attention = attention
        self.tone = tone
        self.text = text
    }

    enum CodingKeys: String, CodingKey {
        case attention, tone, text
    }

    /// `parseSummary` (`:215`): a blank `text` beside an `attention` becomes
    /// `ADMIN_ATTENTION_TEXT`, so the banner always has words.
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let attention = c.healthBool(.attention) ?? false
        self.attention = attention
        self.tone = c.healthString(.tone)
        self.text = c.healthString(.text) ?? (attention ? ShellStrings.adminAttention : "")
    }
}

// MARK: - the banner a payload came without

/// The banner from the incidents alone — `deriveBanner` (`:162`), the
/// server's own rule (`views.py:41`).
///
/// **Only for a payload that came without one**, so a server that dropped the
/// field still shows what is open. The tone ranks tamper over a required
/// container's failure over an optional one's, and a tamper banner carries the
/// sentence about what is paused.
public func deriveHealthBanner(_ incidents: [HealthIncident]) -> HealthIncidentBanner {
    let frozen = incidents.contains { $0.isOpen && $0.isTamper }
    let shown = incidents.filter(\.bannerWorthy)
    guard !shown.isEmpty else { return HealthIncidentBanner(frozen: frozen) }
    let tone: String
    if shown.contains(where: \.isTamper) {
        tone = "tamper"
    } else if shown.contains(where: { $0.severity == "error" }) {
        tone = "error"
    } else {
        tone = "warn"
    }
    var text = shown.count == 1
        ? (shown[0].summary ?? HealthPanelStrings.bannerOneContainer)
        : "\(shown.count)\(HealthPanelStrings.bannerManySuffix)"
    if tone == "tamper" { text += HealthPanelStrings.bannerTamperTail }
    return HealthIncidentBanner(
        show: true,
        tone: tone,
        text: text,
        count: shown.count,
        incidentIDs: shown.map(\.id),
        frozen: frozen
    )
}

// MARK: - how much this phone may see

/// How much this phone may see — `IncidentTier` (`:229`): the household list,
/// the open summary, or nothing (not read yet, no server, or a server older
/// than the guard).
public enum HealthTier: String, Equatable, Sendable, CaseIterable {
    case none
    case open
    case household

    /// The same fact in the shape ``guardCheck(_:)`` reads
    /// (`ContainerGuardState.Tier`), which is the seam `HealthChecks.swift`
    /// was written against and which this item fills.
    public var guardTier: ContainerGuardState.Tier {
        switch self {
        case .none: return .none
        case .open: return .open
        case .household: return .household
        }
    }
}

/// What the Containers part shows — `ContainersView` (`:232`).
public enum HealthContainersView: String, Equatable, Sendable, CaseIterable {
    case guest
    case household
    case none
}

/// `containersView` (`:236`).
///
/// **A shared screen (the kitchen tablet) gets the guest line whatever it
/// holds** — like the banner, never what failed (Android review L1). That
/// first branch is the privacy half of this function and the reason it is not
/// simply a rename of the tier.
public func healthContainersView(tier: HealthTier, shared: Bool) -> HealthContainersView {
    if shared && tier != .none { return .guest }
    switch tier {
    case .household: return .household
    case .open: return .guest
    case .none: return .none
    }
}

// MARK: - the shell's banner, as much of it as the check needs

/// The strip at the top of every shell — `ShellBanner` (`:266`).
///
/// The panel does not draw it: `ShellModel.chrome` passes
/// `containerBanner: nil` (`ShellModel.swift:438`) and that file is not this
/// item's to edit (DECISIONS, "Needs a shared change" 3). It is here because
/// ``guardCheck(_:)`` is defined in terms of it — `bannerShows` and
/// `bannerTone` are `shellBanner(state, shared: false)`'s — so the check
/// cannot be fed without computing it.
public struct HealthShellBanner: Equatable, Sendable {

    public let show: Bool
    public let tone: ContainerGuardState.BannerTone?
    public let text: String
    public let frozen: Bool

    public init(
        show: Bool = false,
        tone: ContainerGuardState.BannerTone? = nil,
        text: String = "",
        frozen: Bool = false
    ) {
        self.show = show
        self.tone = tone
        self.text = text
        self.frozen = frozen
    }
}

/// `toneOf` (`:273`): anything that is not `tamper` or `warn` is an error,
/// **including a tone the server has not got round to sending**.
public func healthBannerTone(_ raw: String?) -> ContainerGuardState.BannerTone {
    switch raw {
    case "tamper": return .tamper
    case "warn": return .warn
    default: return .error
    }
}

/// `shellBanner(state, shared:)` (`:286`).
///
/// The household tier uses the server's banner as given; the open tier its
/// summary (`err` red, `warn` amber). On a `shared` screen the words are only
/// `ADMIN_ATTENTION_TEXT`, like Home's problem rows there: a passer-by learns
/// that an admin is needed, **not which container or why**.
public func healthShellBanner(
    tier: HealthTier,
    payload: HealthIncidentsPayload?,
    summary: HealthIncidentSummary?,
    shared: Bool
) -> HealthShellBanner {
    let hidden = HealthShellBanner()
    switch tier {
    case .household:
        guard let banner = payload?.banner, banner.show else { return hidden }
        return HealthShellBanner(
            show: true,
            tone: healthBannerTone(banner.tone),
            text: shared
                ? ShellStrings.adminAttention
                : (banner.text.isEmpty ? ShellStrings.adminAttention : banner.text),
            frozen: banner.frozen || payload?.frozen == true
        )
    case .open:
        guard let summary, summary.attention else { return hidden }
        return HealthShellBanner(
            show: true,
            tone: summary.tone == "warn" ? .warn : .error,
            text: shared
                ? ShellStrings.adminAttention
                : (summary.text.isEmpty ? ShellStrings.adminAttention : summary.text)
        )
    case .none:
        return hidden
    }
}

// MARK: - the details screen's words

/// The `kind` in a few words — `kindWords` (`:315`), the web Containers
/// page's `CtrKindWords`.
///
/// An unknown kind reads with its underscores as spaces, which is the
/// Kotlin's own fallback: a server that learns a new kind says something
/// readable rather than nothing.
public func healthKindWords(_ kind: String?) -> String {
    switch kind {
    case "missing": return HealthPanelStrings.kindMissing
    case "stopped": return HealthPanelStrings.kindStopped
    case "restarting": return HealthPanelStrings.kindRestarting
    case "unhealthy": return HealthPanelStrings.kindUnhealthy
    case "oneshot_failed": return HealthPanelStrings.kindOneshotFailed
    case "daemon": return HealthPanelStrings.kindDaemon
    case "image": return HealthPanelStrings.kindImage
    case "unknown": return HealthPanelStrings.kindUnknown
    case "config": return HealthPanelStrings.kindConfig
    case "outdated": return HealthPanelStrings.kindOutdated
    case .none: return HealthPanelStrings.kindNone
    case .some(let other): return other.replacingOccurrences(of: "_", with: " ")
    }
}

/// The line under an incident — `incidentMeta` (`:336`):
/// `#41 · tampering · different image · domovoi-searxng · opened 2m ago`,
/// then `acknowledged` for an open one an admin acknowledged, or
/// `cleared`/`resolved` and when for a closed one.
///
/// - Parameter rel: how a timestamp is worded, so a test drives the clock.
///   ``relTime(_:nowMs:)`` is what the screen passes. `relTime(nil)` is a bare
///   em dash (`Format.swift:123`), so an incident with no `opened_at` reads
///   `opened —` rather than `opened ` with nothing after it — which is
///   Android's behaviour too.
public func healthIncidentMeta(_ incident: HealthIncident, rel: (String?) -> String) -> String {
    var parts = [
        "\(HealthPanelStrings.idPrefix)\(incident.id)",
        incident.isTamper ? HealthPanelStrings.tamperingWord : HealthPanelStrings.failureWord,
        healthKindWords(incident.kind),
    ]
    if let container = incident.container { parts.append(container) }
    parts.append("\(HealthPanelStrings.openedPrefix)\(rel(incident.openedAt))")
    if incident.isOpen {
        if incident.acknowledged { parts.append(HealthPanelStrings.acknowledgedWord) }
    } else {
        let how = incident.resolution == "cleared"
            ? HealthPanelStrings.clearedWord
            : HealthPanelStrings.resolvedWord
        parts.append("\(how) \(rel(incident.resolvedAt ?? incident.clearedAt))")
    }
    return parts.joined(separator: HealthPanelStrings.metaSeparator)
}

/// The dashboard's own Health, in a browser — `dashboardUrl`
/// (`HomeModels.kt:552`), which iOS had no port of until this item.
///
/// nil for a phone with no saved server, which is Android's `url == null`
/// branch — and the reason the button is **not drawn** rather than drawn and
/// toasting (DECISIONS, "The dashboard button opens Safari").
public func healthDashboardURL(serverURL: String?, hash: String) -> String? {
    var base = (serverURL ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    while base.hasSuffix("/") { base.removeLast() }
    guard !base.isEmpty else { return nil }
    return "\(base)/\(hash)"
}

// MARK: - Kotlin's three JsonObject readers

/// `JsonObject.str` / `.bool` / `.long` (`ContainerIncidents.kt:114-126`),
/// over a `KeyedDecodingContainer`.
///
/// Every one of them is **absent-tolerant**: a missing key, a null, or a value
/// of the wrong type is nil rather than a thrown decode, because the Kotlin's
/// parser reads a `JsonObject` by hand for exactly that reason — "the backend
/// evolves" (`HomeModels.kt:25`). The one key that is not optional is
/// ``HealthIncident/id``, and its absence is the row being dropped.
extension KeyedDecodingContainer {

    /// A present, non-null, **non-blank** string (`str`, `:116`).
    ///
    /// Blank is nil, which matters: `label: ""` must read as absent so
    /// ``HealthIncidentSummary``'s `text` falls back to the admin sentence and
    /// an incident row falls back to its kind.
    ///
    /// Only a JSON string is read. Kotlin's `str` would also return the digits
    /// of a number, but the server sends a string or null for every field this
    /// is used on (`views.py:84`), so a coercion here would have no caller and
    /// a comment is cheaper than one.
    func healthString(_ key: Key) -> String? {
        guard let raw = (try? decodeIfPresent(String.self, forKey: key)) ?? nil else { return nil }
        return raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : raw
    }

    /// A boolean, or the strings Kotlin's `booleanOrNull` and its `"1"`/`"0"`
    /// fallback accept (`bool`, `:118`).
    func healthBool(_ key: Key) -> Bool? {
        if let raw = (try? decodeIfPresent(Bool.self, forKey: key)) ?? nil { return raw }
        guard let text = (try? decodeIfPresent(String.self, forKey: key)) ?? nil else { return nil }
        switch text.lowercased() {
        case "true", "1": return true
        case "false", "0": return false
        default: return nil
        }
    }

    /// An integer, or a string that is one (`long`, `:126`).
    func healthInt64(_ key: Key) -> Int64? {
        if let raw = (try? decodeIfPresent(Int64.self, forKey: key)) ?? nil { return raw }
        guard let text = (try? decodeIfPresent(String.self, forKey: key)) ?? nil else { return nil }
        return Int64(text)
    }
}
