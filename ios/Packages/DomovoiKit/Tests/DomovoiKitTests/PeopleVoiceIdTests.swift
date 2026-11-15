import Foundation
import XCTest
@testable import DomovoiKit

/// Voice ID's "needs to learn voices again" state (`GET /api/people/voice-id`,
/// `Person.voice_relearn`; V061) on the People screens: the household notice,
/// the person's notice, and the roster dot.
///
/// The first half mirrors Android's `VoiceIdNoticeTest` (2095fd5f, with
/// 2766a325's possessive) one case for one case. The second is
/// ``PeopleModel``'s read: alongside the roster and refreshed with it, quiet
/// on a server without the route, keeping what is shown on any other failure,
/// and never landing under the wrong house ([[read-after-await-wrong-key]]).
@MainActor
final class PeopleVoiceIdTests: XCTestCase {

    private func person(_ json: String) throws -> Person {
        try JSONDecoder().decode(Person.self, from: Data(json.utf8))
    }

    private func status(_ json: String) -> VoiceIdStatus? {
        PeopleVoiceId.read(Data(json.utf8))
    }

    private let ready = VoiceIdStatus(ready: true)
    private static let offProblem = "Internet access is off, so the voice model can't be fetched."
    private let off = VoiceIdStatus(
        ready: false,
        problem: "Internet access is off, so the voice model can't be fetched."
    )
    private let alex = Person(id: 1, name: "Alex", voiceRelearn: true)
    private let jordan = Person(id: 3, name: "Jordan", voiceProfileCount: 2)

    /// Spelt out with Android's characters: ASCII `'` in "can't", U+2019 in
    /// "won’t", curly quotes and U+2026 around "my name is …".
    private let relearnBody = "Voice recognition was upgraded, and voices it knew before can't be used. "
        + "Until each person says \u{201C}my name is \u{2026}\u{201D} in a room and answers yes, "
        + "Domovoi won\u{2019}t know who is speaking. "

    private func relearning(_ count: Int) -> VoiceIdStatus {
        VoiceIdStatus(ready: true, relearn: count)
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - Android's VoiceIdNoticeTest: parsing

    func testVoiceRelearnIsReadAndMissingMeansFalse() throws {
        XCTAssertTrue(try person(#"{"id":1,"name":"Alex","voice_relearn":true}"#).voiceRelearn)
        XCTAssertFalse(try person(#"{"id":1,"name":"Alex","voice_relearn":false}"#).voiceRelearn)
        // An older server has no such field.
        XCTAssertFalse(try person(#"{"id":1,"name":"Alex","voice_profile_count":3}"#).voiceRelearn)
        XCTAssertFalse(try person(#"{"id":1,"name":"Alex","voice_relearn":null}"#).voiceRelearn)
    }

    func testStatusIsReadWithMissingFieldsHarmless() {
        XCTAssertEqual(
            status(#"{"ready":true,"problem":null,"relearn":2}"#),
            VoiceIdStatus(ready: true, problem: nil, relearn: 2)
        )
        XCTAssertEqual(
            status(#"{"ready":false,"problem":"\#(Self.offProblem)","relearn":0}"#),
            off
        )
        // Missing pieces never claim voice ID is off, nor that anyone must relearn.
        XCTAssertEqual(status("{}"), VoiceIdStatus(ready: true, problem: nil, relearn: 0))
        XCTAssertEqual(
            status(#"{"ready":null,"relearn":null,"extra":1}"#),
            VoiceIdStatus(ready: true, relearn: 0)
        )
        XCTAssertNil(status("[]"))
        XCTAssertNil(status("null"))
    }

    /// iOS's own: a `1` is not a `true` (the `NSNumber` bridge), so an
    /// answer whose `ready` is a number is not a status, and is kept out
    /// rather than read as "on".
    func testAReadyThatIsNotABooleanIsNotAStatus() {
        XCTAssertNil(status(#"{"ready":1,"relearn":0}"#))
        XCTAssertNil(status(#"{"ready":true,"relearn":"two"}"#))
        XCTAssertNil(status("not json"))
    }

    // MARK: - loading: a missing route is quiet

    func testAServerWithoutTheRouteIsAbsent() async {
        let missing = await PeopleVoiceId.load {
            throw DomovoiError.httpError(ApiFailure(status: 404, message: #"404: {"detail":"Not Found"}"#))
        }
        XCTAssertEqual(missing, .absent)
        // An older server takes "voice-id" for /api/people/{person_id} and refuses a non-number.
        let refused = await PeopleVoiceId.load {
            throw DomovoiError.httpError(ApiFailure(status: 422, message: "422: Unprocessable Entity"))
        }
        XCTAssertEqual(refused, .absent)
        XCTAssertNil(PeopleVoiceId.next(shown: nil, read: .absent))
        XCTAssertNil(PeopleVoiceId.next(shown: relearning(2), read: .absent))
        XCTAssertNil(PeopleVoiceId.notice(PeopleVoiceId.next(shown: nil, read: .absent)))
    }

    func testOtherFailuresKeepWhatIsShown() async {
        let trouble = await PeopleVoiceId.load {
            throw DomovoiError.serverTrouble(ApiFailure(status: 500, message: "500: Internal Server Error"))
        }
        XCTAssertEqual(trouble, .failed)
        let unreachable = await PeopleVoiceId.load {
            throw DomovoiError.unreachable("Failed to connect to 192.168.1.20:6369")
        }
        XCTAssertEqual(unreachable, .failed)
        let notAnObject = await PeopleVoiceId.load { Data("[1]".utf8) }
        XCTAssertEqual(notAnObject, .failed)
        let shown = relearning(2)
        XCTAssertEqual(PeopleVoiceId.next(shown: shown, read: .failed), shown)
        XCTAssertNil(PeopleVoiceId.next(shown: nil, read: .failed))
    }

    func testAnAnswerReplacesWhatIsShown() async {
        let read = await PeopleVoiceId.load { Data(#"{"ready":true,"relearn":3}"#.utf8) }
        XCTAssertEqual(read, .known(relearning(3)))
        XCTAssertEqual(PeopleVoiceId.next(shown: off, read: read), relearning(3))
    }

    // MARK: - the household notice

    func testOffSaysSoWithTheServersProblemAsIs() {
        XCTAssertEqual(
            PeopleVoiceId.notice(off),
            VoiceNotice(title: "Voice recognition is off", body: Self.offProblem, isOff: true)
        )
        // Off wins over relearn: nobody is asked to introduce themselves.
        XCTAssertEqual(
            PeopleVoiceId.notice(VoiceIdStatus(ready: false, problem: Self.offProblem, relearn: 4))?.title,
            PeopleStrings.voiceIdOffTitle
        )
        XCTAssertEqual(
            PeopleVoiceId.notice(VoiceIdStatus(ready: false, problem: " ")),
            VoiceNotice(title: "Voice recognition is off", body: nil, isOff: true)
        )
        XCTAssertEqual(
            PeopleVoiceId.notice(VoiceIdStatus(ready: false, problem: nil)),
            VoiceNotice(title: "Voice recognition is off", body: nil, isOff: true)
        )
    }

    func testRelearnSaysOnePersonInTheSingular() {
        XCTAssertEqual(
            PeopleVoiceId.notice(relearning(1)),
            VoiceNotice(title: "Domovoi needs to learn voices again", body: relearnBody + "1 person still has to.")
        )
    }

    func testRelearnSaysNPeopleInThePlural() {
        XCTAssertEqual(
            PeopleVoiceId.notice(relearning(3)),
            VoiceNotice(title: "Domovoi needs to learn voices again", body: relearnBody + "3 people still have to.")
        )
    }

    func testNothingToSayWhenReadyAndNobodyRelearns() {
        XCTAssertNil(PeopleVoiceId.notice(nil))
        XCTAssertNil(PeopleVoiceId.notice(ready))
        XCTAssertNil(PeopleVoiceId.notice(relearning(-1)))
    }

    // MARK: - the person's notice

    func testPersonNoticeNamesThem() {
        XCTAssertEqual(
            PeopleVoiceId.personNotice(alex, voiceId: ready),
            VoiceNotice(
                title: "Not recognized by voice yet",
                body: "Voice recognition was upgraded, so Domovoi has to learn Alex\u{2019}s voice again. "
                    + "To be recognized again, Alex can say \u{201C}my name is Alex\u{201D} in a room and answer yes."
            )
        )
        // Trimmed, as Android's `name.trim()` is (the stub's person 4 is "alex ").
        XCTAssertEqual(
            PeopleVoiceId.personNotice(Person(id: 4, name: "alex ", voiceRelearn: true), voiceId: ready)?.body,
            "Voice recognition was upgraded, so Domovoi has to learn alex\u{2019}s voice again. "
                + "To be recognized again, alex can say \u{201C}my name is alex\u{201D} in a room and answer yes."
        )
    }

    func testPersonNoticeWithoutANameSaysTheirAndThey() {
        XCTAssertEqual(
            PeopleVoiceId.personNotice(Person(id: 1, name: " ", voiceRelearn: true), voiceId: ready)?.body,
            "Voice recognition was upgraded, so Domovoi has to learn their voice again. "
                + "To be recognized again, they can say \u{201C}my name is \u{2026}\u{201D} in a room and answer yes."
        )
    }

    func testPersonNoticeOnlyWhenTheyRelearnAndVoiceIdIsNotOff() {
        XCTAssertNil(PeopleVoiceId.personNotice(jordan, voiceId: ready))
        XCTAssertNil(PeopleVoiceId.personNotice(alex, voiceId: off))
        // Voice ID's state unknown (older server, not read yet): the person's own flag decides.
        XCTAssertEqual(PeopleVoiceId.personNotice(alex, voiceId: nil)?.title, PeopleStrings.personRelearnTitle)
        XCTAssertEqual(PeopleVoiceId.personNotice(alex, voiceId: nil)?.isOff, false)
    }

    // MARK: - the roster dot

    func testRosterDotForSomeoneWhoRelearnsUnlessVoiceIdIsOff() {
        XCTAssertEqual(
            PeopleStrings.relearnDotDescription,
            "has to say \u{201C}my name is \u{2026}\u{201D} again to be recognized by voice"
        )
        XCTAssertTrue(PeopleVoiceId.showsRelearnDot(alex, voiceId: ready))
        XCTAssertTrue(PeopleVoiceId.showsRelearnDot(alex, voiceId: nil))
        XCTAssertFalse(PeopleVoiceId.showsRelearnDot(alex, voiceId: off))
        XCTAssertFalse(PeopleVoiceId.showsRelearnDot(jordan, voiceId: ready))
    }

    // MARK: - an older server changes nothing

    func testAnOlderServerShowsNothingNew() async throws {
        let p = try person(#"{"id":3,"name":"Jordan","voice_profile_count":2}"#)
        let read = await PeopleVoiceId.load {
            throw DomovoiError.httpError(ApiFailure(status: 404, message: "404: Not Found"))
        }
        let shown = PeopleVoiceId.next(shown: nil, read: read)
        XCTAssertNil(PeopleVoiceId.notice(shown))
        XCTAssertNil(PeopleVoiceId.personNotice(p, voiceId: shown))
        XCTAssertFalse(PeopleVoiceId.showsRelearnDot(p, voiceId: shown))
        XCTAssertTrue(
            PersonVoiceRooms.shows([PersonVoiceRoom(roomID: "kitchen", current: true)], person: p, voiceId: shown)
        )
    }

    // MARK: - the model's read

    private let server = "http://10.0.0.42:6369"
    private let otherServer = "http://10.0.0.99:6369"

    /// The house. Person 4 relearns. `/api/people/voice-id` answers
    /// ``voiceIdStatus`` with ``voiceIdBody``; the other server (10.0.0.99)
    /// always answers ready with nobody to relearn, so an answer filed under
    /// the wrong house shows.
    private final class House: Sendable {
        let rosterStatus = Shared<Int>(200)
        let voiceIdStatus = Shared<Int>(200)
        let voiceIdBody = Shared<String>(#"{"ready":true,"problem":null,"relearn":1}"#)
        /// How many roster reads were answered. Each answer carries its
        /// number in Jordan's notes, so a test can tell that **its** read came
        /// back: the voice-id answer is applied before the rows publish.
        let rosterReads = Shared<Int>(0)

        func roster(_ read: Int) -> String {
            """
            [{"id":1,"name":"Jordan","notes":"read \(read)","voice_profile_count":3,"voice_relearn":false},
             {"id":4,"name":"alex ","voice_profile_count":0,"voice_relearn":true}]
            """
        }

        func answer(_ request: URLRequest) -> StubURLProtocol.Answer {
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            switch parts.path {
            case "/api/health":
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            case "/api/people":
                let status = rosterStatus.current
                guard status == 200 else { return .json(#"{"detail":"down"}"#, status: status) }
                let read: Int = rosterReads.mutate { (count: inout Int) -> Int in
                    count += 1
                    return count
                }
                return .json(roster(read))
            case "/api/people/voice-id":
                if url.host == "10.0.0.99" { return .json(#"{"ready":true,"problem":null,"relearn":0}"#) }
                let status = voiceIdStatus.current
                return status == 200
                    ? .json(voiceIdBody.current)
                    : .json(#"{"detail":"Not Found"}"#, status: status)
            case "/api/denylist":
                return .json("[]")
            default:
                return .json("{}", status: 404)
            }
        }

        func install() {
            StubURLProtocol.dispatch { request in self.answer(request) }
        }
    }

    private func rig() -> (PeopleModel, House, InMemoryShellStore) {
        let house = House()
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        return (PeopleModel(in: container, clock: { 0 }), house, store)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func nothingHappens() async {
        for _ in 0..<3000 { await Task.yield() }
    }

    private func voiceIdReads() -> Int {
        StubURLProtocol.recorded.filter { $0.url?.path == "/api/people/voice-id" }.count
    }

    /// Start, and wait for the roster and voice ID's first answer.
    private func started(_ model: PeopleModel, file: StaticString = #filePath, line: UInt = #line) async {
        model.start()
        await settleUntil({ model.roster.answered && model.voiceId != nil }, "the first read", file: file, line: line)
    }

    /// Re-read the roster (a retry, a push) and wait until **that** read
    /// came back: its rows are on screen (their number is in Jordan's
    /// notes), or, when the roster is down, its failure is.
    private func reread(
        _ model: PeopleModel,
        _ house: House,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        let before = house.rosterReads.current
        let failing = house.rosterStatus.current != 200
        model.roster.refresh()
        await settleUntil(
            {
                if failing { return model.roster.error != nil }
                return model.people.first?.notes == "read \(before + 1)"
            },
            what, file: file, line: line
        )
        await nothingHappens()
    }

    func testVoiceIdIsReadWithTheRosterAndRefreshedWithIt() async {
        let (model, _, _) = rig()
        await started(model)

        XCTAssertEqual(model.voiceId, relearning(1))
        XCTAssertEqual(voiceIdReads(), 1)
        XCTAssertEqual(model.voiceIdNotice?.title, PeopleStrings.voiceRelearnTitle)
        XCTAssertEqual(model.voiceIdNotice?.body, relearnBody + "1 person still has to.")
        XCTAssertEqual(model.people.map(\.voiceRelearn), [false, true])
        XCTAssertTrue(model.showsRelearnDot(model.people.last ?? jordan))
        XCTAssertFalse(model.showsRelearnDot(model.people.first ?? alex))

        // The roster's push re-reads both.
        model.received(StateEvent(type: "people.last_seen.changed", frame: "{}"))
        await settleUntil({ self.voiceIdReads() == 2 }, "the push's re-read")
    }

    /// Off: the household notice says so, and the person's dot goes.
    func testOffHidesTheDotAndSaysSo() async {
        let (model, house, _) = rig()
        house.voiceIdBody.set(#"{"ready":false,"problem":"\#(Self.offProblem)","relearn":1}"#)
        await started(model)

        XCTAssertEqual(model.voiceIdNotice, VoiceNotice(title: PeopleStrings.voiceIdOffTitle, body: Self.offProblem, isOff: true))
        XCTAssertFalse(model.showsRelearnDot(model.people.last ?? alex))
    }

    /// 404 and 422 (a server older than the route) clear what was shown.
    func testAServerWithoutTheRouteClearsTheState() async {
        for status in [404, 422] {
            let (model, house, _) = rig()
            await started(model)
            XCTAssertEqual(model.voiceId, relearning(1))

            house.voiceIdStatus.set(status)
            await reread(model, house, "the \(status) re-read")

            XCTAssertNil(model.voiceId, "a \(status) kept the last state")
            XCTAssertNil(model.voiceIdNotice)
            XCTAssertNil(model.roster.error, "the voice-id \(status) failed the roster")
            model.stop()
        }
    }

    /// Any other failure, or an answer that is not a status, keeps the last
    /// state, and fails nothing else.
    func testOtherFailuresKeepTheState() async {
        let (model, house, _) = rig()
        await started(model)

        house.voiceIdStatus.set(500)
        await reread(model, house, "the 500 re-read")
        XCTAssertEqual(model.voiceId, relearning(1), "a 500 dropped the state")
        XCTAssertNil(model.roster.error, "the voice-id 500 failed the roster")

        house.voiceIdStatus.set(200)
        house.voiceIdBody.set("[1]")
        await reread(model, house, "the not-an-object re-read")
        XCTAssertEqual(model.voiceId, relearning(1), "an answer that isn't an object dropped the state")
    }

    /// A roster read that failed uses nothing it read: the voice-id answer
    /// beside it is dropped with it (Android's `coroutineScope`), so a 404
    /// read alongside a failed roster does not clear the notice.
    func testAFailedRosterReadLeavesTheStateAlone() async {
        let (model, house, _) = rig()
        await started(model)

        house.rosterStatus.set(503)
        house.voiceIdStatus.set(404)
        await reread(model, house, "the failed roster re-read")

        XCTAssertNotNil(model.roster.error)
        XCTAssertEqual(model.voiceId, relearning(1), "a failed roster read applied its voice-id answer")
    }

    /// The screen's own server switch drops the old house's state at once.
    func testMovingHouseClearsTheState() async {
        let (model, _, _) = rig()
        await started(model)
        XCTAssertNotNil(model.voiceId)

        model.serverChanged()
        XCTAssertNil(model.voiceId, "the old house's voice ID state survived the move")
        XCTAssertNil(model.voiceIdNotice)
    }

    /// The answer from the house the phone has left never shows in the one it
    /// moved to: the switch happens **inside** the voice-id round trip.
    func testAnAnswerFromTheHouseLeftBehindIsNotShown() async {
        let (model, house, store) = rig()
        let park = parkRequest(
            to: "/api/people/voice-id",
            answering: { .json(#"{"ready":true,"problem":null,"relearn":2}"#) },
            otherwise: { house.answer($0) }
        )

        model.start()
        await settleUntil({ park.arrived.current }, "the first house's voice-id read")
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        XCTAssertTrue(store.setServerURL(otherServer))
        park.release.set(true)
        await settleUntil({ model.roster.answered }, "the roster")
        await nothingHappens()

        XCTAssertFalse(park.ranOut.current, "the park outlasted the session timeout")
        XCTAssertNil(model.voiceId, "the first house's voice ID state is shown in the second")

        // The new house's own answer does show.
        house.install()
        await reread(model, house, "the second house's read")
        XCTAssertEqual(model.voiceId, VoiceIdStatus(ready: true, relearn: 0))
    }
}
