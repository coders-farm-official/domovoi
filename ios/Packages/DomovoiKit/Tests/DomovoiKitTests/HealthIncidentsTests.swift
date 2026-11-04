import Foundation
import XCTest
@testable import DomovoiKit

/// The container guard's wire layer and its pure rules: a real
/// `household_payload` decoded key by key, the three derived fields, the row
/// that is dropped, the body that is **not** this payload, the banner a
/// payload came without, and the words an incident row shows.
///
/// Android's `ContainerIncidentsTest.kt` covers the parsers on its side; what
/// it cannot cover is iOS's decoder, which is a different mechanism — a
/// `Decodable` that throws where Kotlin's hand-written reader returns null.
/// Every "absent key" case here is about that difference.
final class HealthIncidentsTests: XCTestCase {

    // MARK: - the server's own body

    /// `GET /api/containers/incidents` for a household caller, in the exact
    /// shape `views.household_payload` serves (`views.py:118`) with
    /// `incident_view`'s seventeen keys per row (`views.py:84`) and **no
    /// admin keys** — a phone never sees `expected`, `found`,
    /// `acknowledged_by`, `cleared_by` or `clear_note` (`views.py:107`).
    private let householdBody = """
    {"incidents": [
       {"id": 41, "class": "tamper", "kind": "image", "severity": "error",
        "label": "the web search helper",
        "summary": "The web search helper is running a different image than the one Domovoi expects.",
        "owner": "core", "container": "domovoi-searxng",
        "opened_at": "2026-11-02T18:53:00+00:00", "last_seen_at": "2026-11-02T18:59:30+00:00",
        "resolved_at": null, "resolution": null,
        "acknowledged": false, "acknowledged_at": null, "cleared_at": null,
        "open": true, "freezes": true, "can_acknowledge": false, "can_clear": true},
       {"id": 40, "class": "failure", "kind": "restarting", "severity": "error",
        "label": "the music player for kitchen",
        "summary": "The music player for kitchen keeps restarting.",
        "owner": "core", "container": "domovoi-mpd-kitchen",
        "opened_at": "2026-11-02T18:30:00+00:00", "last_seen_at": "2026-11-02T18:59:30+00:00",
        "resolved_at": null, "resolution": null,
        "acknowledged": true, "acknowledged_at": "2026-11-02T18:45:00+00:00", "cleared_at": null,
        "open": true, "freezes": false, "can_acknowledge": false, "can_clear": false},
       {"id": 39, "class": "failure", "kind": "oneshot_failed", "severity": "warn",
        "label": "the database migrations",
        "summary": "The database migrations failed (exit code 1).",
        "owner": "core", "container": "domovoi-flyway",
        "opened_at": "2026-11-02T17:00:00+00:00", "last_seen_at": "2026-11-02T17:00:00+00:00",
        "resolved_at": "2026-11-02T18:00:00+00:00", "resolution": "recovered",
        "acknowledged": false, "acknowledged_at": null, "cleared_at": null,
        "open": false, "freezes": false, "can_acknowledge": false, "can_clear": false},
       {"id": 38, "class": "tamper", "kind": "unknown", "severity": "error",
        "label": "an unknown container (domovoi-oops)",
        "summary": "An unknown container (domovoi-oops) is running under Domovoi's names.",
        "owner": "core", "container": "domovoi-oops",
        "opened_at": "2026-11-01T20:00:00+00:00", "last_seen_at": "2026-11-02T18:40:00+00:00",
        "resolved_at": "2026-11-02T18:39:00+00:00", "resolution": "cleared",
        "acknowledged": false, "acknowledged_at": null, "cleared_at": "2026-11-02T18:39:00+00:00",
        "open": false, "freezes": false, "can_acknowledge": false, "can_clear": false}],
     "banner": {"show": true, "tone": "tamper",
                "text": "The web search helper is running a different image than the one Domovoi expects. Plugin installs, updates and room upgrades are paused until an admin reviews it.",
                "count": 2, "incident_ids": [41, 40], "frozen": true},
     "frozen": true, "attention": true}
    """

    private func payload(_ json: String) throws -> HealthIncidentsPayload {
        try JSONDecoder().decode(HealthIncidentsPayload.self, from: Data(json.utf8))
    }

    func testTheServersHouseholdPayloadDecodesKeyByKey() throws {
        let decoded = try payload(householdBody)
        XCTAssertEqual([41, 40, 39, 38], decoded.incidents.map(\.id),
                       "the store's order is kept: open first, newest first, then the last day's resolved")
        XCTAssertTrue(decoded.frozen)
        XCTAssertTrue(decoded.attention)
        XCTAssertEqual(2, decoded.banner.count)
        XCTAssertEqual([41, 40], decoded.banner.incidentIDs)
        XCTAssertEqual("tamper", decoded.banner.tone)
        XCTAssertTrue(decoded.banner.frozen)

        let tamper = decoded.incidents[0]
        XCTAssertTrue(tamper.isTamper)
        XCTAssertTrue(tamper.isRed)
        XCTAssertTrue(tamper.freezes)
        XCTAssertTrue(tamper.isOpen)
        XCTAssertTrue(tamper.bannerWorthy, "an open tamper incident: only clearing hides it")
        XCTAssertEqual("domovoi-searxng", tamper.container)
        XCTAssertEqual("the web search helper", tamper.label)
        XCTAssertEqual("image", tamper.kind)
        XCTAssertEqual("core", tamper.owner)
        XCTAssertEqual("2026-11-02T18:53:00+00:00", tamper.openedAt)
        XCTAssertEqual("2026-11-02T18:59:30+00:00", tamper.lastSeenAt)
        XCTAssertNil(tamper.resolvedAt)
        XCTAssertNil(tamper.resolution)
        XCTAssertFalse(tamper.acknowledged)
        XCTAssertNil(tamper.acknowledgedAt)
        XCTAssertNil(tamper.clearedAt)

        let acknowledged = decoded.incidents[1]
        XCTAssertFalse(acknowledged.isTamper, "class failure")
        XCTAssertTrue(acknowledged.isRed, "severity error is red without being tampering")
        XCTAssertTrue(acknowledged.acknowledged)
        XCTAssertFalse(acknowledged.bannerWorthy, "an acknowledged failure is off the banner")

        let warned = decoded.incidents[2]
        XCTAssertFalse(warned.isOpen)
        XCTAssertFalse(warned.isRed, "an optional container's failure is amber")
        XCTAssertEqual("recovered", warned.resolution)
        XCTAssertFalse(warned.bannerWorthy, "a resolved incident is never on the banner")

        let cleared = decoded.incidents[3]
        XCTAssertEqual("cleared", cleared.resolution)
        XCTAssertEqual("2026-11-02T18:39:00+00:00", cleared.clearedAt)
        XCTAssertFalse(cleared.freezes, "a cleared tamper incident holds nothing back")

        XCTAssertEqual([41, 40], decoded.openIncidents.map(\.id))
        XCTAssertEqual([39, 38], decoded.resolvedIncidents.map(\.id))
    }

    /// The three fields `parseIncident` derives when the server did not send
    /// them (`ContainerIncidents.kt:133`, `:149`, `:153`) — which is a server
    /// older than the field, and on iOS also any server, because a
    /// `Decodable` that required them would throw the row away.
    func testTheDerivedFieldsAreTheKotlinsWhenTheKeysAreAbsent() throws {
        let decoded = try payload("""
        {"incidents": [
          {"id": 1, "class": "tamper", "kind": "config"},
          {"id": 2, "class": "failure", "resolved_at": "2026-11-02T18:00:00+00:00"},
          {"id": 3, "class": "failure", "acknowledged_at": "2026-11-02T18:00:00+00:00"}]}
        """)
        let tamper = decoded.incidents[0]
        XCTAssertTrue(tamper.isOpen, "no resolved_at, so open")
        XCTAssertTrue(tamper.freezes, "an open tamper incident freezes without being told to")
        XCTAssertFalse(tamper.acknowledged)

        let resolved = decoded.incidents[1]
        XCTAssertFalse(resolved.isOpen, "a resolved_at is what closes it")
        XCTAssertFalse(resolved.freezes)

        let acknowledged = decoded.incidents[2]
        XCTAssertTrue(acknowledged.acknowledged, "an acknowledged_at is an acknowledgement")
        XCTAssertTrue(acknowledged.isOpen)
    }

    /// A `class` the app does not know reads as a failure (`:134`), so an
    /// incident of a kind invented later is never silently treated as
    /// tampering — and never silently freezes the house.
    func testAnUnknownClassIsAFailure() throws {
        let decoded = try payload(#"{"incidents": [{"id": 9, "class": "sabotage"}]}"#)
        XCTAssertEqual(healthClassFailure, decoded.incidents[0].cls)
        XCTAssertFalse(decoded.incidents[0].isTamper)
        XCTAssertFalse(decoded.incidents[0].freezes)
    }

    /// `mapNotNull { parseIncident(it) }` (`:204`): **one bad row, not the
    /// read.** The whole list used to be the thing at risk — a `Decodable`
    /// array throws on the first element it cannot read.
    func testARowWithNoUsableIdIsDroppedAndTheOthersSurvive() throws {
        let decoded = try payload("""
        {"incidents": [
          {"id": 41, "class": "tamper"},
          {"class": "failure", "kind": "missing"},
          {"id": null, "kind": "stopped"},
          "not an object",
          {"id": "40", "class": "failure", "kind": "restarting"}]}
        """)
        XCTAssertEqual([41, 40], decoded.incidents.map(\.id),
                       "three unusable rows went; the one with a string id stayed, as Kotlin's long() does")
    }

    /// `parseIncidents` returns **null** for a body with no `incidents`
    /// array (`:201`) — that is how "the route answered something that is not
    /// this" becomes a failure the tier rule can act on rather than an empty
    /// list the screen would draw as a healthy house.
    func testABodyWithNoIncidentsArrayDoesNotDecode() {
        XCTAssertThrowsError(try payload(#"{"banner": {"show": false}, "frozen": false}"#))
        XCTAssertThrowsError(try payload(#"{"incidents": {"id": 1}}"#))
        XCTAssertThrowsError(try payload("[]"))
        XCTAssertThrowsError(try payload(#""a string""#))
    }

    func testAnEmptyIncidentsArrayIsAHealthyHouse() throws {
        let decoded = try payload(#"{"incidents": []}"#)
        XCTAssertTrue(decoded.incidents.isEmpty)
        XCTAssertFalse(decoded.banner.show, "derived from nothing")
        XCTAssertFalse(decoded.frozen)
        XCTAssertFalse(decoded.attention)
    }

    // MARK: - the banner a payload came without

    func testADerivedBannerRanksTamperOverErrorOverWarn() {
        let warn = deriveHealthBanner([HealthIncident(id: 1, severity: "warn")])
        XCTAssertEqual("warn", warn.tone)
        let error = deriveHealthBanner([
            HealthIncident(id: 1, severity: "warn"),
            HealthIncident(id: 2, severity: "error"),
        ])
        XCTAssertEqual("error", error.tone)
        let tamper = deriveHealthBanner([
            HealthIncident(id: 1, severity: "warn"),
            HealthIncident(id: 2, severity: "error"),
            HealthIncident(id: 3, cls: healthClassTamper, severity: "error"),
        ])
        XCTAssertEqual("tamper", tamper.tone)
    }

    func testADerivedBannerSaysWhatOneIncidentSaysAndCountsTheRest() {
        let one = deriveHealthBanner([
            HealthIncident(id: 7, severity: "error", summary: "The database stopped (exit code 1)."),
        ])
        XCTAssertTrue(one.show)
        XCTAssertEqual("The database stopped (exit code 1).", one.text,
                       "one incident speaks for itself")
        XCTAssertEqual(1, one.count)
        XCTAssertEqual([7], one.incidentIDs)
        XCTAssertFalse(one.frozen)

        let none = deriveHealthBanner([HealthIncident(id: 7, severity: "error")])
        XCTAssertEqual(HealthPanelStrings.bannerOneContainer, none.text,
                       "and falls back when it has no summary of its own")

        let many = deriveHealthBanner([
            HealthIncident(id: 7, severity: "error", summary: "one"),
            HealthIncident(id: 8, severity: "warn", summary: "two"),
        ])
        XCTAssertEqual("2\(HealthPanelStrings.bannerManySuffix)", many.text)
        XCTAssertEqual(2, many.count)
    }

    func testADerivedTamperBannerSaysWhatIsPaused() {
        let banner = deriveHealthBanner([
            HealthIncident(id: 7, cls: healthClassTamper, severity: "error", summary: "A different image."),
        ])
        XCTAssertEqual("A different image.\(HealthPanelStrings.bannerTamperTail)", banner.text)
        XCTAssertTrue(banner.frozen)
    }

    /// `frozen` is **any open tamper incident** (`:163`), not the banner's
    /// subject: an acknowledged tamper incident is off the banner and still
    /// holds the freeze.
    func testTheFreezeIsAnyOpenTamperIncidentEvenOneOffTheBanner() {
        let banner = deriveHealthBanner([
            HealthIncident(id: 7, cls: healthClassTamper, acknowledged: true, isOpen: true),
        ])
        XCTAssertTrue(banner.frozen)
        XCTAssertTrue(banner.show, "a tamper incident is banner-worthy acknowledged or not")

        let closed = deriveHealthBanner([
            HealthIncident(id: 7, cls: healthClassTamper, resolvedAt: "x", isOpen: false),
        ])
        XCTAssertFalse(closed.frozen)
        XCTAssertFalse(closed.show)
    }

    /// `count` falls back to the number of ids (`:190`), which is what a
    /// server that sends the list and not the count means.
    func testABannerWithNoCountCountsItsIds() throws {
        let decoded = try payload("""
        {"incidents": [{"id": 1}], "banner": {"show": true, "tone": "warn", "incident_ids": [1, 2, 3]}}
        """)
        XCTAssertEqual(3, decoded.banner.count)
    }

    /// `frozen` and `attention` fall back to the banner's (`:209-210`).
    func testAPayloadWithoutFrozenOrAttentionTakesTheBanners() throws {
        let decoded = try payload("""
        {"incidents": [{"id": 1}], "banner": {"show": true, "tone": "tamper", "frozen": true}}
        """)
        XCTAssertTrue(decoded.frozen)
        XCTAssertTrue(decoded.attention)
    }

    // MARK: - the open tier's answer

    func testTheSummaryRouteDecodesAndFillsItsOwnWords() throws {
        func summary(_ json: String) throws -> HealthIncidentSummary {
            try JSONDecoder().decode(HealthIncidentSummary.self, from: Data(json.utf8))
        }
        let real = try summary(
            #"{"attention": true, "tone": "err", "text": "Something needs an admin's attention."}"#
        )
        XCTAssertTrue(real.attention)
        XCTAssertEqual("err", real.tone, "err, NOT the banner's error — one letter, two routes")
        XCTAssertEqual(ShellStrings.adminAttention, real.text)

        let blank = try summary(#"{"attention": true, "tone": "warn", "text": ""}"#)
        XCTAssertEqual(ShellStrings.adminAttention, blank.text,
                       "a blank text beside an attention still has words")

        let quiet = try summary(#"{"attention": false, "tone": null, "text": ""}"#)
        XCTAssertEqual("", quiet.text)
        XCTAssertNil(quiet.tone)
    }

    // MARK: - who sees what

    /// All six combinations of tier × shared, which is the whole of
    /// `containersView` (`:236`) — and the first branch is a privacy rule,
    /// not a rename: **a shared screen never sees the list**, whatever its
    /// token could read (Android review L1).
    func testEveryTierAndSharedCombination() {
        XCTAssertEqual(.household, healthContainersView(tier: .household, shared: false))
        XCTAssertEqual(.guest, healthContainersView(tier: .household, shared: true))
        XCTAssertEqual(.guest, healthContainersView(tier: .open, shared: false))
        XCTAssertEqual(.guest, healthContainersView(tier: .open, shared: true))
        XCTAssertEqual(HealthContainersView.none, healthContainersView(tier: .none, shared: false))
        XCTAssertEqual(
            HealthContainersView.none, healthContainersView(tier: .none, shared: true),
            "nothing has answered: a shared screen says nothing rather than the guest line"
        )
    }

    // MARK: - the words

    func testEveryKindHasItsWordsIncludingNoneAndAnUnknownOne() {
        XCTAssertEqual("missing", healthKindWords("missing"))
        XCTAssertEqual("stopped", healthKindWords("stopped"))
        XCTAssertEqual("restarting", healthKindWords("restarting"))
        XCTAssertEqual("unhealthy", healthKindWords("unhealthy"))
        XCTAssertEqual("failed", healthKindWords("oneshot_failed"))
        XCTAssertEqual("docker down", healthKindWords("daemon"))
        XCTAssertEqual("different image", healthKindWords("image"))
        XCTAssertEqual("unknown container", healthKindWords("unknown"))
        XCTAssertEqual("risky settings", healthKindWords("config"))
        XCTAssertEqual("older image", healthKindWords("outdated"))
        XCTAssertEqual("problem", healthKindWords(nil))
        XCTAssertEqual(
            "something else entirely", healthKindWords("something_else_entirely"),
            "a kind invented later reads with its underscores as spaces"
        )
    }

    /// The order and the separators of the whole line, with a clock the test
    /// owns: `#id`, class, kind, container, opened — and **nothing else**
    /// while an unacknowledged incident is open.
    func testTheMetaLineOfAnOpenTamperIncidentNamesItsContainer() {
        let line = healthIncidentMeta(
            HealthIncident(
                id: 41, cls: healthClassTamper, kind: "image", severity: "error",
                container: "domovoi-searxng", openedAt: "2026-11-02T18:53:00+00:00"
            ),
            rel: { _ in "7m ago" }
        )
        XCTAssertEqual(
            "#41 · tampering · different image · domovoi-searxng · opened 7m ago", line
        )
    }

    /// …and the real formatter agrees about the one piece the closure stands
    /// in for, so the screen's own `relTime` cannot be the thing that is
    /// wrong.
    func testTheMetaLineUsesTheFormatterTheScreenPasses() {
        let openedAt = "2026-11-02T18:53:00+00:00"
        let nowMs = (isoMs(openedAt) ?? 0) + 7 * 60 * 1000
        let line = healthIncidentMeta(
            HealthIncident(id: 41, kind: "image", openedAt: openedAt),
            rel: { relTime($0, nowMs: nowMs) }
        )
        XCTAssertEqual("#41 · failure · different image · opened 7m ago", line)
    }

    func testTheMetaLineSaysAcknowledgedOnlyWhileItIsOpen() {
        let open = healthIncidentMeta(
            HealthIncident(id: 40, kind: "restarting", acknowledged: true, isOpen: true),
            rel: { _ in "2m ago" }
        )
        XCTAssertEqual("#40 · failure · restarting · opened 2m ago · acknowledged", open)

        let closed = healthIncidentMeta(
            HealthIncident(
                id: 40, kind: "restarting", resolvedAt: "then", resolution: "recovered",
                acknowledged: true, isOpen: false
            ),
            rel: { _ in "1h ago" }
        )
        XCTAssertEqual(
            "#40 · failure · restarting · opened 1h ago · resolved 1h ago", closed,
            "a closed incident says how it closed instead; the acknowledgement is history"
        )
    }

    func testAClearedIncidentSaysClearedAndFallsBackToItsClearedAt() {
        let line = healthIncidentMeta(
            HealthIncident(
                id: 38, cls: healthClassTamper, kind: "unknown", container: "domovoi-oops",
                resolution: "cleared", clearedAt: "2026-11-02T18:39:00+00:00", isOpen: false
            ),
            rel: { iso in iso == nil ? "—" : "20m ago" }
        )
        XCTAssertEqual(
            "#38 · tampering · unknown container · domovoi-oops · opened — · cleared 20m ago", line,
            "no opened_at reads `opened —`, and resolved_at being absent falls back to cleared_at"
        )
    }

    func testAnIncidentWithNoKindSaysProblem() {
        let line = healthIncidentMeta(HealthIncident(id: 5), rel: { _ in "just now" })
        XCTAssertEqual("#5 · failure · problem · opened just now", line)
    }

    func testTheFreezeListIsTheKotlinsFourLinesInOrder() {
        XCTAssertEqual(4, HealthFreezeList.lines.count)
        XCTAssertEqual("plugin installs, upgrades and enabling a plugin", HealthFreezeList.lines[0])
        XCTAssertEqual("pulling and applying Domovoi updates", HealthFreezeList.lines[3])
    }

    // MARK: - the shell's banner, which the check is defined in terms of

    func testTheHouseholdBannerIsTheServersAndASharedScreenGetsOnlyTheSentence() throws {
        let decoded = try payload(householdBody)
        let mine = healthShellBanner(tier: .household, payload: decoded, summary: nil, shared: false)
        XCTAssertTrue(mine.show)
        XCTAssertEqual(.tamper, mine.tone)
        XCTAssertEqual(
            decoded.banner.text, mine.text,
            "the server's own words, as given — including the sentence about what is paused"
        )
        XCTAssertTrue(mine.text.contains("web search helper"))
        XCTAssertTrue(mine.frozen)

        let tablet = healthShellBanner(tier: .household, payload: decoded, summary: nil, shared: true)
        XCTAssertEqual(
            ShellStrings.adminAttention, tablet.text,
            "a passer-by learns an admin is needed, not which container or why"
        )
    }

    func testTheOpenTiersBannerIsItsSummaryAndTheToneIsErrOrWarn() {
        let err = healthShellBanner(
            tier: .open, payload: nil,
            summary: HealthIncidentSummary(attention: true, tone: "err", text: "x"),
            shared: false
        )
        XCTAssertEqual(.error, err.tone, "err on this route means the banner's error")
        let warn = healthShellBanner(
            tier: .open, payload: nil,
            summary: HealthIncidentSummary(attention: true, tone: "warn", text: "x"),
            shared: false
        )
        XCTAssertEqual(.warn, warn.tone)
        let quiet = healthShellBanner(
            tier: .open, payload: nil,
            summary: HealthIncidentSummary(attention: false), shared: false
        )
        XCTAssertFalse(quiet.show)
    }

    func testNothingAnsweredIsNoBanner() {
        XCTAssertFalse(
            healthShellBanner(tier: .none, payload: nil, summary: nil, shared: false).show
        )
    }

    /// An unknown tone is **red**, not hidden (`toneOf`, `:273`): a server
    /// that invents a tone must not make a banner that shows invisibly.
    func testAnUnknownBannerToneIsRed() {
        XCTAssertEqual(.error, healthBannerTone("whatever"))
        XCTAssertEqual(.error, healthBannerTone(nil))
        XCTAssertEqual(.tamper, healthBannerTone("tamper"))
        XCTAssertEqual(.warn, healthBannerTone("warn"))
    }

    // MARK: - the dashboard's address

    func testTheDashboardURLTrimsAndRefusesABlankServer() {
        XCTAssertEqual(
            "http://10.0.0.42:6369/#settings/health",
            healthDashboardURL(serverURL: "http://10.0.0.42:6369/", hash: "#settings/health")
        )
        XCTAssertEqual(
            "http://10.0.0.42:6369/#settings/health",
            healthDashboardURL(serverURL: "  http://10.0.0.42:6369///  ", hash: "#settings/health")
        )
        XCTAssertNil(healthDashboardURL(serverURL: "", hash: "#settings/health"),
                     "no server, no button — the control would not do what it says")
        XCTAssertNil(healthDashboardURL(serverURL: nil, hash: "#settings/health"))
        XCTAssertNil(healthDashboardURL(serverURL: "   ", hash: "#settings/health"))
    }
}
