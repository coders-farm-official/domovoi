import Foundation
import XCTest
@testable import DomovoiKit

/// The Music mix (Up next 1.3.0): the port of Android's `MusicMixTest`
/// (8230ccd2), case for case — the shapes as `web/backend/api/music_mix.py`
/// writes them, whose sliders win, which controls this app may show
/// (wf/mix-gate), how a refusal is said, and the dashboard's words — plus the
/// two models' read-after-await keying, which the iPhone holds in a model
/// where Android holds it in the composable.
@MainActor
final class MusicMixTests: XCTestCase {

    private let base = "http://10.0.0.42:6369"
    private let other = "http://10.0.0.43:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    /// The server the app talks to, movable from inside a round trip.
    final class Live: @unchecked Sendable {
        private let lock = NSLock()
        private var value: String
        init(_ value: String) { self.value = value }
        var server: String {
            get { lock.lock(); defer { lock.unlock() }; return value }
            set { lock.lock(); value = newValue; lock.unlock() }
        }
    }

    private func client(_ live: Live, health: TokenHealth? = nil) -> DomovoiClient {
        DomovoiClient(
            book: ServerBook(activeServer: { live.server }, tokenForServer: { _ in "household-phrase-1234" }),
            gate: AdmitsAnyServer(),
            health: health,
            configuration: StubURLProtocol.session(timeout: 5)
        )
    }

    private func decode<T: Decodable>(_ json: String, as type: T.Type) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    private func body(at index: Int) -> String? {
        let all = StubURLProtocol.recorded
        guard all.indices.contains(index), let data = all[index].httpBody else { return nil }
        return String(decoding: data, as: UTF8.self)
    }

    private static let personAnswer =
        #"{"person_id":4,"override":{"discovery_favorites":5,"close_wander":3},"uses":{"discovery_favorites":5,"close_wander":3}}"#

    /// The toasts a model said.
    @MainActor
    final class Said {
        var all: [String] = []
    }

    // MARK: - the wire

    /// `_mix_answer`: every value off its default, so a misnamed field can't
    /// pass on the default.
    func testDecodesTheHouseholdMixFieldForField() throws {
        let m = try decode("""
        {"discovery_favorites":1,"close_wander":5,"autoplay":false,"list_repeat_min":120,
         "steps":[1,2,3,4,5],"list_repeat_min_range":[20,500],
         "internet":{"similar_artists":true,"popularity":false,"state":"ok"},
         "people_with_own_mix":[4,7]}
        """, as: MusicMix.self)
        XCTAssertEqual(1, m.discoveryFavorites)
        XCTAssertEqual(5, m.closeWander)
        XCTAssertFalse(m.autoplay)
        XCTAssertEqual(120, m.listRepeatMin)
        XCTAssertEqual([1, 2, 3, 4, 5], m.steps)
        XCTAssertEqual([20, 500], m.listRepeatMinRange)
        XCTAssertEqual(true, m.internet.similarArtists)
        XCTAssertEqual(false, m.internet.popularity)
        XCTAssertEqual("ok", m.internet.state)
        XCTAssertEqual([4, 7], m.peopleWithOwnMix)
    }

    /// `_internet` before the core reported: nulls and "unknown".
    func testAnInternetNotReportedYetIsNotKnown() throws {
        let m = try decode("""
        {"discovery_favorites":3,"close_wander":3,"autoplay":true,"list_repeat_min":50,"steps":[1,2,3,4,5],
         "list_repeat_min_range":[20,500],"internet":{"similar_artists":null,"popularity":null,"state":"unknown"},
         "people_with_own_mix":[]}
        """, as: MusicMix.self)
        XCTAssertNil(m.internet.similarArtists)
        XCTAssertEqual(MusicMixStrings.notKnownYet, MusicMixStrings.onOff(m.internet.similarArtists))
        XCTAssertEqual("not known yet", MusicMixStrings.notKnownYet)
        XCTAssertEqual("on", MusicMixStrings.onOff(true))
        XCTAssertEqual("off", MusicMixStrings.onOff(false))
    }

    /// `_person_answer`: {person_id, override, uses}.
    func testDecodesAPersonsMixBothWays() throws {
        let house = try decode(
            #"{"person_id":4,"override":null,"uses":{"discovery_favorites":2,"close_wander":4}}"#, as: PersonMix.self
        )
        XCTAssertEqual(4, house.personID)
        XCTAssertFalse(house.ownMix, "null override: the household's")
        XCTAssertEqual(MixSliders(discoveryFavorites: 2, closeWander: 4), house.uses)
        // A stored override may hold one slider only (mix.py person_override).
        let own = try decode(
            #"{"person_id":7,"override":{"close_wander":5},"uses":{"discovery_favorites":2,"close_wander":5}}"#,
            as: PersonMix.self
        )
        XCTAssertTrue(own.ownMix)
        XCTAssertNil(own.override?.discoveryFavorites)
        XCTAssertEqual(5, own.override?.closeWander)
        XCTAssertEqual(MixSliders(discoveryFavorites: 2, closeWander: 5), own.uses)
    }

    // MARK: - whose sliders win

    func testTurningTheHouseholdsMixOffStartsFromWhatThePersonUsesNow() {
        // They used the household's (2, 4): their own starts there, not at 3/3.
        let uses = MixSliders(discoveryFavorites: 2, closeWander: 4)
        XCTAssertEqual(uses, MusicMixRules.ownMix(from: uses))
        XCTAssertEqual(
            #"{"override":{"discovery_favorites":2,"close_wander":4}}"#,
            String(decoding: MusicMixRules.personBody(MusicMixRules.ownMix(from: uses)), as: UTF8.self)
        )
        // Moving one slider keeps the other as it is used now.
        XCTAssertEqual(MixSliders(discoveryFavorites: 5, closeWander: 4), MusicMixRules.withStep(uses, discovery: true, step: 5))
        XCTAssertEqual(MixSliders(discoveryFavorites: 2, closeWander: 1), MusicMixRules.withStep(uses, discovery: false, step: 1))
        // Back to the household's: null, never the household's numbers copied.
        XCTAssertEqual(#"{"override":null}"#, String(decoding: MusicMixRules.personBody(nil), as: UTF8.self))
        // Missing values (an older answer) fall back to the middle, as the web does.
        XCTAssertEqual(MixSliders(discoveryFavorites: 3, closeWander: 3), MusicMixRules.ownMix(from: MixSliders()))
    }

    // MARK: - which controls a phone shows

    func testAPhoneNeverOffersTheHouseholdsMixOrTheInternetSwitches() {
        XCTAssertFalse(musicMixAppIsAdmin, "the app holds no admin session")
        XCTAssertEqual(MixRights(household: false, internet: false), MusicMixRules.rights())
        XCTAssertEqual(MixRights(household: true, internet: true), MusicMixRules.rights(admin: true))
    }

    func testOnlyThePersonThisPhoneIsGetsSliders() {
        let alex = DeviceOwner(id: 4, name: "Alex")
        XCTAssertTrue(MusicMixRules.editable(personID: 4, owner: alex, shared: false), "this phone is Alex's")
        XCTAssertFalse(MusicMixRules.editable(personID: 7, owner: alex, shared: false), "someone else's page")
        XCTAssertFalse(MusicMixRules.editable(personID: 4, owner: nil, shared: false), "a phone that is nobody's")
        XCTAssertFalse(MusicMixRules.editable(personID: 4, owner: alex, shared: true), "a shared screen is nobody's")
        XCTAssertTrue(MusicMixRules.editable(personID: 7, owner: nil, shared: false, admin: true), "an admin may change anyone's")
    }

    // MARK: - refusals

    private func refusal(_ status: Int, _ body: String) -> DomovoiError {
        let f = ApiFailure(status: status, message: "\(status): \(body)", body: body)
        switch status {
        case 401, 403: return .refused(f)
        case 500...599: return .serverTrouble(f)
        default: return .httpError(f)
        }
    }

    func testARefusedPersonMixIsSaidInTheServersWords() {
        XCTAssertEqual("Only an admin, or this person's own phone, can change their mix.", MusicMixStrings.notYours)
        XCTAssertEqual("something the server said",
                       MusicMixRules.writeRefusal(refusal(403, #"{"detail":"something the server said"}"#)))
        XCTAssertEqual(MusicMixStrings.notYours, MusicMixRules.writeRefusal(refusal(403, "")))
        XCTAssertNil(MusicMixRules.writeRefusal(refusal(500, "")), "not a refusal: the usual failure line")
        XCTAssertNil(MusicMixRules.writeRefusal(.unreachable("down")))
    }

    func testTheAdminGates401IsAnAdminsNotALostPairing() {
        XCTAssertEqual(MusicMixStrings.adminCanChangeThis,
                       MusicMixRules.writeRefusal(refusal(401, #"{"detail":"admin session required"}"#)))
        XCTAssertNil(MusicMixRules.writeRefusal(refusal(401, #"{"detail":"X-Device-Token or admin session required"}"#)),
                     "the household token itself: the pairing flow's")
    }

    /// Through the real client: `PUT /api/music/mix`'s 401 neither raises
    /// the pairing screen nor marks the household token refused.
    func testAHouseholdMixPut401LeavesThePairingAlone() async {
        let health = TokenHealth()
        let api = client(Live(base), health: health)
        StubURLProtocol.enqueue(.json(#"{"detail":"admin session required"}"#, status: 401))
        do throws(DomovoiError) {
            try await api.send(method: "PUT", path: MusicMixPath.household, body: Data(#"{"autoplay":false}"#.utf8))
            XCTFail("a phone's PUT is refused")
        } catch {
            XCTAssertFalse(api.pairingRequired, "no pairing screen")
            XCTAssertEqual(false, error.failure?.deviceTokenRequired, "no pairing screen")
            XCTAssertFalse(health.isRejected(base), "the token is not marked refused")
            XCTAssertEqual(MusicMixStrings.adminCanChangeThis, MusicMixRules.writeRefusal(error))
        }
    }

    /// `PUT /api/people/{id}/music-mix` names this phone (the server matches
    /// its owner), and a 403 comes back in the server's words.
    func testAPersonMixPutNamesThisPhoneAndSaysTheServers403() async throws {
        let api = client(Live(base))
        StubURLProtocol.enqueue(.json(Self.personAnswer))
        let saved = try await savePersonMix(
            client: api, server: base, deviceID: "ios-1a2b", claimKey: "held-key", personID: 4,
            override: MixSliders(discoveryFavorites: 5, closeWander: 3)
        )
        let sent = try XCTUnwrap(StubURLProtocol.recorded.first)
        XCTAssertEqual("PUT", sent.httpMethod)
        XCTAssertEqual("/api/people/4/music-mix", sent.url?.path)
        XCTAssertEqual("ios-1a2b", sent.value(forHTTPHeaderField: deviceIDHeader))
        // As for clearing listening history (security review 2026-11-22, L40).
        XCTAssertEqual("held-key", sent.value(forHTTPHeaderField: claimKeyHeader))
        XCTAssertEqual(#"{"override":{"discovery_favorites":5,"close_wander":3}}"#, body(at: 0))
        XCTAssertEqual(MixSliders(discoveryFavorites: 5, closeWander: 3), saved.uses)

        StubURLProtocol.enqueue(.json(#"{"detail":"\#(MusicMixStrings.notYours)"}"#, status: 403))
        do throws(DomovoiError) {
            _ = try await savePersonMix(client: api, server: base, deviceID: "ios-1a2b", personID: 7, override: nil)
            XCTFail("refused")
        } catch {
            XCTAssertEqual(MusicMixStrings.notYours, MusicMixRules.writeRefusal(error))
            XCTAssertFalse(api.pairingRequired)
        }
    }

    // MARK: - Autoplay: the person's choice here, else the household's

    func testEffectiveAutoplayIsTheChoiceThenTheHouseholdThenOn() {
        XCTAssertTrue(MusicMixRules.effectiveAutoplay(choice: nil, householdDefault: nil))
        XCTAssertFalse(MusicMixRules.effectiveAutoplay(choice: nil, householdDefault: false))
        XCTAssertTrue(MusicMixRules.effectiveAutoplay(choice: nil, householdDefault: true))
        XCTAssertTrue(MusicMixRules.effectiveAutoplay(choice: true, householdDefault: false))
        XCTAssertFalse(MusicMixRules.effectiveAutoplay(choice: false, householdDefault: true))
    }

    func testTheHouseholdsDefaultIsKeptByServer() {
        let d = MixDefaults()
        d.heard("http://10.0.0.51:6369/", MusicMix(autoplay: false))
        XCTAssertEqual(false, d.autoplayDefault(for: "http://10.0.0.51:6369"))
        XCTAssertNil(d.autoplayDefault(for: "http://10.0.0.59:6369"), "another house's switch learns nothing")
    }

    func testThisPhonesLineUnderTheDefault() {
        XCTAssertEqual("This phone follows this until you switch autoplay in its queue.", MusicMixStrings.phoneAutoplayNote(nil))
        XCTAssertEqual("This phone: autoplay on (switched in its queue).", MusicMixStrings.phoneAutoplayNote(true))
        XCTAssertEqual("This phone: autoplay off (switched in its queue).", MusicMixStrings.phoneAutoplayNote(false))
    }

    // MARK: - the words (music_mix.jsx)

    func testTheDashboardsWords() {
        XCTAssertEqual("Discovery", MusicMixStrings.discovery.left)
        XCTAssertEqual("Favorites", MusicMixStrings.discovery.right)
        XCTAssertEqual("Stay close", MusicMixStrings.wander.left)
        XCTAssertEqual("Wander", MusicMixStrings.wander.right)
        XCTAssertEqual("discovery or favorites", MusicMixStrings.discovery.label)
        XCTAssertEqual("stay close or wander", MusicMixStrings.wander.label)
        XCTAssertEqual(
            [
                "mostly songs you haven’t heard in a while", "more songs you haven’t heard in a while",
                "a balance (the default)", "more of what you play most", "mostly what you play most",
            ],
            (1...5).map { MusicMixStrings.discovery.said($0) }
        )
        XCTAssertEqual(
            [
                "stays close to the song playing", "mostly close to the song playing", "a balance (the default)",
                "wanders more across your library", "wanders widely across your library",
            ],
            (1...5).map { MusicMixStrings.wander.said($0) }
        )
        XCTAssertEqual("a balance (the default)", MusicMixStrings.wander.said(nil))
        XCTAssertEqual("The mix", MusicMixStrings.mixTitle)
        XCTAssertEqual("People can set their own in Settings → People; everyone else gets these.", MusicMixStrings.mixSub)
        XCTAssertEqual("Playing on", MusicMixStrings.playingOnTitle)
        XCTAssertEqual("Autoplay when a playlist ends", MusicMixStrings.autoplayLabel)
        XCTAssertEqual("No repeats within", MusicMixStrings.noRepeatsLabel)
        XCTAssertEqual("From the internet", MusicMixStrings.internetTitle)
        XCTAssertEqual("Optional. Up next works from your library alone without them.", MusicMixStrings.internetSub)
        XCTAssertEqual("Similar artists from the internet", MusicMixStrings.similarArtistsLabel)
        XCTAssertEqual("Song popularity from the internet", MusicMixStrings.popularityLabel)
        XCTAssertEqual("Use the household’s mix", MusicMixStrings.useHouseholds)
        XCTAssertEqual("An admin can change this.", MusicMixStrings.adminCanChangeThis)
        XCTAssertEqual("An admin can change these.", MusicMixStrings.adminCanChangeThese)
        XCTAssertEqual("An admin, or Alex’s own phone, can change this.", MusicMixStrings.readOnlyNote("Alex"))
        XCTAssertEqual(
            "Used when Up next plays for Alex: “listening as” them, their phone, or their voice in a room.",
            MusicMixStrings.usedWhen("Alex")
        )
        XCTAssertEqual("Alex uses the household’s mix.", MusicMixStrings.usesHouseholds("Alex"))
        XCTAssertEqual("Alex has their own mix.", MusicMixStrings.hasOwn("Alex"))
        XCTAssertEqual("120 songs", MusicMixStrings.noRepeatsValue(120))
    }

    // MARK: - the models: an answer is drawn only for the server it was for

    /// The household's mix read from A, answered after the app moved to B:
    /// not drawn on B's screen — but A's Autoplay default is kept under A.
    func testAHouseholdMixAnsweredAfterTheServerChangedIsNotDrawn() async {
        let live = Live(base)
        let defaults = MixDefaults()
        let model = HouseholdMixModel(client: client(live), server: { live.server }, defaults: defaults)
        let other = self.other
        StubURLProtocol.dispatch { _ in
            live.server = other
            return .json(#"{"discovery_favorites":1,"close_wander":5,"autoplay":false}"#)
        }
        await model.read()
        XCTAssertNil(model.mix, "A's mix is not drawn while the app talks to B")
        XCTAssertNil(model.problem)
        XCTAssertEqual(false, defaults.autoplayDefault(for: base), "kept under the server it was asked of")
        XCTAssertNil(defaults.autoplayDefault(for: other), "B learnt nothing from A")
    }

    func testAHouseholdMixReadIsDrawnAndAnOlderServerSaysSo() async {
        let live = Live(base)
        let defaults = MixDefaults()
        let model = HouseholdMixModel(client: client(live), server: { live.server }, defaults: defaults)
        StubURLProtocol.enqueue(.json(#"{"detail":"Not Found"}"#, status: 404))
        await model.read()
        XCTAssertNil(model.mix)
        XCTAssertEqual(MusicMixStrings.olderServer, model.problem)
        StubURLProtocol.enqueue(.json(#"{"discovery_favorites":4,"autoplay":false}"#))
        await model.read()
        XCTAssertEqual(4, model.mix?.discoveryFavorites)
        XCTAssertEqual(false, model.mix?.autoplay)
        XCTAssertNil(model.problem)
        XCTAssertEqual("/api/music/mix", StubURLProtocol.recorded.last?.url?.path)
    }

    private func personModel(_ live: Live, said: @escaping @MainActor (String) -> Void = { _ in }) -> PersonMixModel {
        PersonMixModel(client: client(live), server: { live.server }, deviceID: { "ios-1a2b" }, say: said)
    }

    /// Their own phone moves a slider: the PUT names this phone, the answer
    /// is drawn and its toast said.
    func testASavedMixIsDrawnAndSaid() async {
        let live = Live(base)
        let said = Said()
        let model = personModel(live) { said.all.append($0) }
        model.show(4)
        StubURLProtocol.enqueue(.json(#"{"person_id":4,"override":null,"uses":{"discovery_favorites":2,"close_wander":4}}"#))
        await model.read()
        XCTAssertEqual(false, model.mix?.ownMix)
        StubURLProtocol.enqueue(.json(Self.personAnswer))
        await model.setStep(discovery: true, step: 5, name: "Alex")
        XCTAssertEqual(#"{"override":{"discovery_favorites":5,"close_wander":4}}"#, body(at: 1), "the other slider stays")
        XCTAssertEqual("ios-1a2b", StubURLProtocol.header(deviceIDHeader, at: 1))
        XCTAssertEqual(true, model.mix?.ownMix)
        XCTAssertEqual(["Alex’s mix saved"], said.all)
        XCTAssertFalse(model.busy)
    }

    /// A 403 (the phone stopped being theirs): the server's words under the
    /// mix, and the mix read again.
    func testARefusedSaveSaysTheServersWordsAndReadsAgain() async {
        let live = Live(base)
        let said = Said()
        let model = personModel(live) { said.all.append($0) }
        model.show(4)
        StubURLProtocol.enqueue(
            .json(#"{"person_id":4,"override":null,"uses":{"discovery_favorites":3,"close_wander":3}}"#),
            .json(#"{"detail":"\#(MusicMixStrings.notYours)"}"#, status: 403),
            .json(#"{"person_id":4,"override":null,"uses":{"discovery_favorites":3,"close_wander":3}}"#)
        )
        await model.read()
        await model.useHousehold(false, name: "Alex")
        XCTAssertEqual(MusicMixStrings.notYours, model.refusal)
        XCTAssertEqual(["GET", "PUT", "GET"], StubURLProtocol.recorded.map { $0.httpMethod ?? "" })
        XCTAssertTrue(said.all.isEmpty, "nothing was saved")
        XCTAssertFalse(model.busy)
    }

    /// A save answered after the app moved to another server: nothing drawn
    /// there, and no toast claiming it.
    func testASaveAnsweredAfterTheServerChangedIsNotDrawnOrSaid() async {
        let live = Live(base)
        let said = Said()
        let model = personModel(live) { said.all.append($0) }
        model.show(4)
        StubURLProtocol.enqueue(.json(#"{"person_id":4,"override":null,"uses":{"discovery_favorites":3,"close_wander":3}}"#))
        await model.read()
        let other = self.other
        let answer = Self.personAnswer
        StubURLProtocol.dispatch { _ in
            live.server = other
            return .json(answer)
        }
        await model.setStep(discovery: true, step: 5, name: "Alex")
        XCTAssertNil(model.mix, "A's answer is not drawn on B")
        XCTAssertTrue(said.all.isEmpty, "no toast for a save on a server the app left")
        live.server = base
        XCTAssertEqual(false, model.mix?.ownMix, "back on A: the last answer drawn there, not the late one")
    }

}
