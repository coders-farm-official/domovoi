import Foundation
import Synchronization

// ---------------------------------------------------------------------------
// The container guard's engine, the half that is not Android — the port of
// `alerts/IncidentEngine.kt:213-243`'s `refresh()` (read at `898b3af2`).
//
// Android's engine is process-wide and also posts notifications, keeps a
// notified book and runs a background tick. **None of that is here**: the
// notification half needs a local-notification authorisation, which means
// `project.yml`, which this item must not edit (DECISIONS, "No notifications,
// and nothing implying one"), and the `alerts` item owns it. What is here is
// the two reads and the tier rule, as a **pure function over two results**,
// so that every branch — the `keepList` one above all — is a package test
// rather than a screen behaviour nothing can see.
// ---------------------------------------------------------------------------

/// What one of the two reads did — `IncidentEngine.Read` (`:190-201`).
///
/// ``ok`` carries an **optional** value on purpose: `read { parse(...) }`
/// wraps a parse that may return null, so "the route answered 200 with
/// something that is not this payload" is `Read.Ok(null)` and not a failure.
/// On iOS that is `DomovoiError.notDomovoi`, which `DomovoiClient.decode`
/// throws for a 2xx body it cannot read (`DomovoiClient.swift:683`).
public enum HealthGuardRead<Value: Sendable>: Sendable {

    /// The request completed. nil means the body was not this shape.
    case ok(Value?)

    /// The server answered with a status that is not 2xx.
    case answered(status: Int)

    /// Nothing answered: no route to the house, a timeout, a dropped packet.
    case failed
}

extension HealthGuardRead: Equatable where Value: Equatable {}

/// What the panel and ``guardCheck(_:)`` read — `IncidentState`
/// (`ContainerIncidents.kt:245`) without the `serverKey`.
///
/// **The key is not a field here.** Android keeps it on the state because one
/// process-wide `StateFlow` serves every server the phone has ever seen, and
/// `refresh()` compares `activeKey()` with the key it started under. Here the
/// snapshot lives in an ``ApiRead``, which already owns the server address as
/// its key (`ApiRead.swift:143-152`) and cancels a read whose server changed,
/// and ``HealthGuardMemory`` carries the key for the one thing that needs it:
/// the previous snapshot the `keepList` rule reads. A second copy of the key
/// on the value is how a reply for server A lands under server B — the top
/// defect class on this track.
public struct HealthGuardSnapshot: Equatable, Sendable {

    public let tier: HealthTier

    /// The server has answered at least once — either read, or a 404.
    public let loaded: Bool

    /// The household payload, on ``HealthTier/household``.
    public let payload: HealthIncidentsPayload?

    /// The open summary, on ``HealthTier/open``.
    public let summary: HealthIncidentSummary?

    public init(
        tier: HealthTier = .none,
        loaded: Bool = false,
        payload: HealthIncidentsPayload? = nil,
        summary: HealthIncidentSummary? = nil
    ) {
        self.tier = tier
        self.loaded = loaded
        self.payload = payload
        self.summary = summary
    }

    /// `IncidentState.incidents` (`:256`) — in the server's order, never
    /// re-sorted.
    public var incidents: [HealthIncident] { payload?.incidents ?? [] }

    public var openIncidents: [HealthIncident] { incidents.filter(\.isOpen) }

    public var resolvedIncidents: [HealthIncident] { incidents.filter { !$0.isOpen } }

    /// `IncidentState.frozen` (`:259`).
    public var frozen: Bool { payload?.frozen == true }

    /// The banner the shell would draw for a household phone.
    public var shellBanner: HealthShellBanner {
        healthShellBanner(tier: tier, payload: payload, summary: summary, shared: false)
    }

    /// **The seam `HealthChecks.swift:209` was written for, filled.**
    ///
    /// That type says "porting the engine later means filling this in and
    /// changes nothing in the rules", and it carries exactly the five things
    /// ``guardCheck(_:)`` reads. So this property is the whole wiring: the
    /// check itself is called unchanged, and `HealthChecks.swift` is not
    /// edited by this item.
    ///
    /// `incidentSummaries` keeps the **last** row for a repeated id, which
    /// cannot happen (`id` is a primary key) and is a decision rather than a
    /// trap only because `Dictionary(uniqueKeysWithValues:)` would crash if
    /// it ever did.
    public var guardState: ContainerGuardState {
        let banner = shellBanner
        return ContainerGuardState(
            loaded: loaded,
            tier: tier.guardTier,
            bannerShows: banner.show,
            bannerTone: banner.tone,
            incidentIDs: payload?.banner.incidentIDs ?? [],
            incidentSummaries: Dictionary(
                incidents.map { ($0.id, $0.summary) },
                uniquingKeysWith: { _, last in last }
            )
        )
    }
}

// MARK: - the tier rule

/// `IncidentEngine.refresh()`'s decision (`:213-243`), as a function of what
/// the two reads did and what this server last showed.
///
/// ```
/// household .ok(payload)            -> household, loaded, the payload
///                                      (and the summary is NEVER asked for)
/// refused = household .answered(401 | 403)
/// the summary is read HERE, keepList or not
/// keepList = previous is household && !refused
///   keepList                        -> the previous snapshot, untouched
///   summary .ok(summary)            -> open, loaded, the summary
///   summary .ok(nil) | .answered    -> none, loaded
///   nothing answered, nothing before-> none, NOT loaded
///   nothing answered, something before -> the previous snapshot
/// ```
///
/// **The summary is a closure, and where it is called is part of the rule.**
/// `refresh()` returns before reading it when the list parsed (`:225`) — one
/// request on the common path, not two — and reads it *before* testing
/// `keepList` when the list did not (`:228`), which a test can see because
/// the closure can count its calls. A version that took two values could not
/// express either, and a version that duplicated the first branch in the
/// model would be the second mechanism nobody reads.
///
/// ### The two branches worth reading twice
///
/// 1. **`keepList`** — a paired phone whose read merely *failed* keeps the
///    list it has; only a 401 or a 403 drops it to the guest line. The
///    inverted version silently downgrades a working phone on one dropped
///    packet, and the words it would then show (`pair this phone to see what
///    Domovoi watches`) are false about it.
/// 2. **`summary .ok(nil) | .answered` is `loaded`, with tier `none`** — a
///    404 from both routes is a server older than the guard. The branch is
///    still Android's to the field: the route answered, so `loaded`; this
///    phone was told nothing about any container, so the tier is `none`.
///
///    What changed on 2026-11-03 is what the **check** makes of it. `loaded`
///    with a hidden banner used to read `every container Domovoi expects
///    looks right` — green, for something nobody checked — and Kamron's
///    answers 7 and 24 are `not checked yet` on both apps. So
///    ``guardCheck(_:)`` asks ``ContainerGuardState/checkedSomething``
///    (`loaded && tier != .none`) and this state now reads grey. The engine
///    is not diverged from; the sentence drawn over it is, and the Android
///    half of the same answer is the android unit's.
///
/// - Parameter previous: what this **same server** last showed, or nil. The
///   caller is responsible for the "same server" part — see
///   ``HealthGuardMemory``.
public func healthGuardOutcome(
    household: HealthGuardRead<HealthIncidentsPayload>,
    previous: HealthGuardSnapshot?,
    summary readSummary: () async -> HealthGuardRead<HealthIncidentSummary>
) async -> HealthGuardSnapshot {
    if case .ok(let payload) = household, let payload {
        return HealthGuardSnapshot(tier: .household, loaded: true, payload: payload)
    }
    let refused: Bool
    if case .answered(let status) = household {
        refused = status == 401 || status == 403
    } else {
        refused = false
    }
    let summary = await readSummary()
    if previous?.tier == .household && !refused { return previous ?? HealthGuardSnapshot() }
    if case .ok(let value) = summary, let value {
        return HealthGuardSnapshot(tier: .open, loaded: true, summary: value)
    }
    switch summary {
    case .ok, .answered:
        return HealthGuardSnapshot(tier: .none, loaded: true)
    case .failed:
        // `current == null -> IncidentState(serverKey = key)`, which is NOT
        // loaded: nothing has answered for this server yet, and a phone with
        // no answer must not read as a house with no incidents.
        return previous ?? HealthGuardSnapshot()
    }
}

// MARK: - the two reads

/// The guard's two reads. **Both untracked**, which is the one thing about
/// them that is not obvious.
///
/// `IncidentEngine.kt:219` says why: "neither read may raise or clear the
/// pairing screen". A *tracked* 401 sets `DomovoiClient.pairing`, and the
/// shell routes a pairing refusal to the pairing screen
/// (`DomovoiClient.swift:628`) — so an unpaired phone opening the one screen
/// whose job is to say what it cannot see would be thrown off it. And a 200
/// on the open summary would *clear* the flag for a phone that really does
/// need to pair.
public enum HealthGuardApi {

    /// `IncidentsApi.INCIDENTS_PATH` — `require_device_read`
    /// (`containers.py:88`).
    public static let incidentsPath = "/api/containers/incidents"

    /// `IncidentsApi.SUMMARY_PATH` — open, no credential (`containers.py:80`).
    public static let summaryPath = "/api/containers/summary"

    public static func incidents(_ client: DomovoiClient) async -> HealthGuardRead<HealthIncidentsPayload> {
        do {
            return .ok(try await client.getUntracked(incidentsPath, as: HealthIncidentsPayload.self))
        } catch {
            return classify(error)
        }
    }

    public static func summary(_ client: DomovoiClient) async -> HealthGuardRead<HealthIncidentSummary> {
        do {
            return .ok(try await client.getUntracked(summaryPath, as: HealthIncidentSummary.self))
        } catch {
            return classify(error)
        }
    }

    /// Android's three `catch` arms, in iOS's terms.
    ///
    /// `.notDomovoi` is the interesting one: the client throws it for a 2xx
    /// body it could not decode, which is exactly `parseIncidents` returning
    /// null — the route answered, with something that is not this. Everything
    /// that carries a status is `Read.Answered`; everything else is
    /// `Read.Failed`.
    static func classify<Value: Sendable>(_ error: DomovoiError) -> HealthGuardRead<Value> {
        if case .notDomovoi = error { return .ok(nil) }
        if let status = error.failure?.status { return .answered(status: status) }
        return .failed
    }
}

// MARK: - the previous snapshot, and the server it belongs to

/// The last snapshot and **which server it was read from**, for the one rule
/// that needs the previous one: `keepList`.
///
/// A reference with a lock rather than a field on the model, because the
/// read's `fetch` runs off the main actor (``ApiRead`` takes a `@Sendable`
/// closure) and must read the previous snapshot **before its first `await`**,
/// under the key it captured at the same moment. That is the
/// read-after-await-wrong-key rule in its positive form: the key is taken
/// before the round trip, and ``snapshot(for:)`` answers nil for any other
/// key — so a slow read of house A that lands after a switch to house B
/// cannot hand B house A's incident list as its "previous".
///
/// `@unchecked` is not needed: `Mutex` is `Sendable` and the one stored
/// property is only ever read and written under it, never across a suspension.
public final class HealthGuardMemory: Sendable {

    private struct Held: Sendable {
        let key: String
        let snapshot: HealthGuardSnapshot
    }

    private let held = Mutex<Held?>(nil)

    public init() {}

    /// What `key`'s server last showed, or nil — **including when something is
    /// held for a different server**.
    public func snapshot(for key: String) -> HealthGuardSnapshot? {
        let current = held.withLock { $0 }
        guard let current, current.key == key else { return nil }
        return current.snapshot
    }

    public func remember(_ snapshot: HealthGuardSnapshot, for key: String) {
        held.withLock { $0 = Held(key: key, snapshot: snapshot) }
    }

    /// For a test, and for a model that is starting again.
    public func forget() {
        held.withLock { $0 = nil }
    }
}
