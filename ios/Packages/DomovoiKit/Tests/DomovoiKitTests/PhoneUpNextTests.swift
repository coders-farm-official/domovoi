import XCTest
@testable import DomovoiKit

// Up next on the phone itself (P9): the port of Android's PhoneUpNextTest,
// PlayerUpNextTest, ListenUpNextTest and PhoneUpNextUiTest (4fb53334).
// Every case drives the real ``PlayerModel`` (over the fake engine) as the
// host, with a fake ``UpNextApi`` that can change the world inside a round
// trip — the only way a read-after-await case can fail on the broken code.

// MARK: - the fakes

@MainActor
final class FakeUpNextApi: UpNextApi {

    struct Call: Equatable {
        let what: String
        let server: String
        let id: String?
        let body: String?
    }

    var calls: [Call] = []

    var startAnswer: Result<UpNextSession, DomovoiError> = .success(UpNextSession(id: "s1", mode: "library"))
    /// Answered in order; the last one again when it runs out.
    var nextAnswers: [Result<UpNextBatch, DomovoiError>] = [.success(UpNextBatch())]
    var feedbackAnswer: Result<[Int], DomovoiError> = .success([])
    var patchAnswer: Result<UpNextSession?, DomovoiError> = .success(nil)
    var readAnswer: Result<UpNextSession, DomovoiError> = .success(UpNextSession(id: "s1"))
    var moveAnswer: Result<UpNextSession?, DomovoiError> = .success(nil)

    /// Run inside the next round trip, before the answer comes back.
    var during: (@MainActor () -> Void)?
    /// The same, for the next refill only.
    var duringNext: (@MainActor () -> Void)?

    private func record(_ what: String, _ server: String, _ id: String?, _ body: Data?) {
        calls.append(Call(what: what, server: server, id: id, body: body.map { String(decoding: $0, as: UTF8.self) }))
        let hook = during
        during = nil
        hook?()
        if what == "next" {
            let refill = duringNext
            duringNext = nil
            refill?()
        }
    }

    func count(_ what: String) -> Int { calls.filter { $0.what == what }.count }

    func bodies(_ what: String) -> [[String: Any]] {
        calls.filter { $0.what == what }.compactMap { call in
            call.body.flatMap { try? JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any] }
        }
    }

    func start(server: String, body: Data) async throws -> UpNextSession {
        record("start", server, nil, body)
        await Task.yield()
        return try startAnswer.get()
    }

    func next(server: String, id: String, haveThrough: Int) async throws -> UpNextBatch {
        record("next", server, id, Data(#"{"have_through_seq":\#(haveThrough)}"#.utf8))
        await Task.yield()
        let answer = nextAnswers.first ?? .success(UpNextBatch())
        if nextAnswers.count > 1 { nextAnswers.removeFirst() }
        return try answer.get()
    }

    func feedback(server: String, id: String, seq: Int, kind: String) async throws -> [Int] {
        record("feedback", server, id, Data(#"{"kind":"\#(kind)","seq":\#(seq)}"#.utf8))
        await Task.yield()
        return try feedbackAnswer.get()
    }

    func patch(server: String, id: String, body: Data) async throws -> UpNextSession? {
        record("patch", server, id, body)
        await Task.yield()
        return try patchAnswer.get()
    }

    func read(server: String, id: String) async throws -> UpNextSession {
        record("read", server, id, nil)
        await Task.yield()
        return try readAnswer.get()
    }

    func move(server: String, id: String, target: Data) async throws -> UpNextSession? {
        record("move", server, id, target)
        await Task.yield()
        return try moveAnswer.get()
    }
}

/// The app as the session sees it, every answer movable.
@MainActor
final class UpNextWorld {
    var server = "http://10.0.0.42:6369"
    var paired = true
    var autoplay = true
    var pick: String?
    var shared = false
    var listenerSet: [String?] = []
    var people: [UpNextPerson] = []

    var env: PhoneUpNextEnv {
        PhoneUpNextEnv(
            server: { [unowned self] in self.server },
            paired: { [unowned self] in self.paired },
            deviceID: { "ios-phone-1" },
            autoplay: { [unowned self] in self.autoplay },
            saveAutoplay: { [unowned self] in self.autoplay = $0 },
            listenerPick: { [unowned self] _ in self.pick },
            shared: { [unowned self] _ in self.shared },
            setListener: { [unowned self] in self.listenerSet.append($0) },
            people: { [unowned self] _ in self.people }
        )
    }
}

/// A player with Up next wired, its fake server and its world.
@MainActor
final class UpNextRig {
    let h: PlayerHarness
    let api: FakeUpNextApi
    let world: UpNextWorld
    let upNext: PhoneUpNext
    private let box: TaskBox

    var model: PlayerModel { h.model }

    init() {
        let h = PlayerHarness()
        let api = FakeUpNextApi()
        let world = UpNextWorld()
        let box = TaskBox()
        let upNext = PhoneUpNext(api: api, host: h.model, env: world.env, launch: { work in
            box.tasks.append(Task { @MainActor in await work() })
        })
        self.h = h
        self.api = api
        self.world = world
        self.box = box
        self.upNext = upNext
        h.model.upNext = upNext
    }

    @MainActor
    final class TaskBox {
        var tasks: [Task<Void, Never>] = []
    }

    /// Let every call launched so far (and every call those launch) answer.
    func drain() async {
        for _ in 0..<50 {
            let open = box.tasks
            box.tasks = []
            if open.isEmpty {
                await Task.yield()
                if box.tasks.isEmpty { return }
                continue
            }
            for task in open { await task.value }
        }
    }
}

func unTrack(_ id: Int64, _ title: String, artist: String = "Orbis Quill") -> PlayerItem {
    PlayerItem.fromTrack(id: id, title: title, artist: artist, durationSec: 200)
}

func unPick(_ seq: Int, _ id: Int64, artist: String = "Nidra Vane", reason: String? = "same artist") -> UpNextPick {
    UpNextPick(seq: seq, trackID: id, title: "pick \(seq)", artist: artist, reasonLabel: reason, status: "queued")
}

func unFailure(_ status: Int) -> DomovoiError {
    let f = ApiFailure(status: status, message: "\(status): no", body: #"{"detail":"no"}"#)
    switch status {
    case 401, 403: return .refused(f)
    case 500...599: return .serverTrouble(f)
    default: return .httpError(f)
    }
}

// MARK: - the session

@MainActor
final class PhoneUpNextTests: XCTestCase {

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        PlayerHouse().install()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        super.tearDown()
    }

    private let list = [unTrack(501, "Veldrassen Overture"), unTrack(503, "Thrumming Ashkeep"), unTrack(502, "Pellucid Marrow")]

    private func start(_ r: UpNextRig) -> [String: Any]? {
        r.api.bodies("start").last?["start"] as? [String: Any]
    }

    // MARK: starts (the four kinds of play)

    func testASongTappedInAListStartsWithItsContext() async {
        let r = UpNextRig()
        let ctx = UpNextContext(kind: "playlist", ref: "41", name: "Longwater Drive")
        r.model.playFromList(list, at: 1, context: ctx)
        await r.drain()
        XCTAssertEqual(r.model.queue.map(\.itemID), [503], "the tapped song plays alone, before any pick")
        let s = start(r)
        XCTAssertEqual(s?["track_id"] as? Int, 503)
        let context = s?["context"] as? [String: Any]
        XCTAssertEqual(context?["kind"] as? String, "playlist")
        XCTAssertEqual(context?["ref"] as? String, "41")
        XCTAssertEqual(context?["name"] as? String, "Longwater Drive")
        XCTAssertNil(s?["whole_list"])
        let body = r.api.bodies("start").last
        XCTAssertEqual((body?["target"] as? [String: Any])?["kind"] as? String, "device")
        XCTAssertEqual((body?["target"] as? [String: Any])?["device_id"] as? String, "ios-phone-1")
        XCTAssertEqual(body?["autoplay"] as? Bool, true, "autoplay is sent on every start")
    }

    func testAnIdsListSendsItsSongsCappedAt500() {
        let ids = (1...700).map { Int64($0) }
        let s = UpNextStart.fromList(trackID: 3, context: UpNextContext(kind: "ids", name: "results"), listIDs: ids)
        XCTAssertEqual(s.context?.trackIDs?.count, 500)
        XCTAssertEqual(s.context?.trackIDs?.first, 1, "a tapped list sends its first 500")
        let w = UpNextStart.wholeList(context: UpNextContext(kind: "ids", name: "results"), listIDs: ids)
        XCTAssertEqual(w.context?.trackIDs?.count, 500)
        XCTAssertEqual(w.context?.trackIDs?.first, 201, "a whole list sends its LAST 500, what continue seeds from")
        XCTAssertEqual(w.trackID, 1)
        let q = UpNextStart.queue(ids)
        XCTAssertEqual(q.context?.trackIDs?.count, 500)
        XCTAssertEqual(q.context?.name, "this queue")
    }

    func testASongOnItsOwnIsLibraryMode() async {
        let r = UpNextRig()
        r.model.playFromList([unTrack(505, "Sablewind Figure")], at: 0, context: nil)
        await r.drain()
        let s = start(r)
        XCTAssertEqual(s?["track_id"] as? Int, 505)
        XCTAssertNil(s?["context"], "no list: library mode")
    }

    func testAWholeListIsContinueModeFromItsFirstSong() async {
        let r = UpNextRig()
        r.model.playList(list, context: UpNextContext(kind: "genre", ref: "brindlecore", name: "Brindlecore"))
        await r.drain()
        XCTAssertEqual(r.model.queue.map(\.queueOrigin), [.wholeList, .wholeList, .wholeList])
        let s = start(r)
        XCTAssertEqual(s?["whole_list"] as? Bool, true)
        XCTAssertEqual(s?["track_id"] as? Int, 501)
        XCTAssertEqual((s?["context"] as? [String: Any])?["ref"] as? String, "brindlecore")
    }

    func testAQueueAddedOneByOneStartsAtFiveLeftWhileAutoplayIsOn() async {
        let r = UpNextRig()
        r.model.enqueue((1...8).map { unTrack(Int64($0), "t\($0)") })
        await r.drain()
        XCTAssertEqual(r.api.count("start"), 0, "eight songs: seven left, more than five")
        r.model.jump(to: 2)
        await r.drain()
        XCTAssertEqual(r.api.count("start"), 1, "five left: a session of its own")
        let s = start(r)
        XCTAssertEqual(s?["whole_list"] as? Bool, true)
        XCTAssertEqual((s?["context"] as? [String: Any])?["name"] as? String, "this queue")
        XCTAssertEqual((s?["context"] as? [String: Any])?["track_ids"] as? [Int], Array(1...8))
    }

    func testAQueueAddedOneByOneStartsNothingWithAutoplayOff() async {
        let r = UpNextRig()
        r.world.autoplay = false
        r.model.enqueue([unTrack(1, "a"), unTrack(2, "b")])
        await r.drain()
        XCTAssertEqual(r.api.count("start"), 0)
    }

    func testNoStartForAQueueWithNoLibrarySongOrAnUnpairedApp() async {
        let r = UpNextRig()
        r.model.play([PlayerSeed.station])
        await r.drain()
        XCTAssertEqual(r.api.count("start"), 0, "radio is no library song")
        r.world.paired = false
        r.model.playFromList([unTrack(1, "a")], at: 0, context: nil)
        await r.drain()
        XCTAssertEqual(r.api.count("start"), 0, "an unpaired app asks for nothing")
    }

    func testTheListenerFieldsGoWithTheStart() async {
        let r = UpNextRig()
        r.world.pick = "7"
        r.model.playFromList([unTrack(1, "a")], at: 0, context: nil)
        await r.drain()
        XCTAssertEqual(r.api.bodies("start").last?["listener_person_id"] as? Int, 7)
        r.world.pick = ListenerPicks.household
        r.model.playFromList([unTrack(1, "a")], at: 0, context: nil)
        await r.drain()
        XCTAssertEqual(r.api.bodies("start").last?["as_household"] as? Bool, true)
        XCTAssertNil(r.api.bodies("start").last?["listener_person_id"])
        r.world.shared = true
        r.model.playFromList([unTrack(1, "a")], at: 0, context: nil)
        await r.drain()
        XCTAssertNil(r.api.bodies("start").last?["as_household"], "a shared screen names nobody")
    }

    func testTheStartsSongsGetTheSessionAndThePicksJoinAsAuto() async {
        let r = UpNextRig()
        r.api.startAnswer = .success(UpNextSession(id: "s9", mode: "list", picks: [unPick(1, 601), unPick(2, 602)], haveThroughSeq: 2))
        r.model.playFromList(list, at: 0, context: UpNextContext(kind: "playlist", ref: "41", name: "L"))
        await r.drain()
        XCTAssertEqual(r.model.queue.map(\.itemID), [501, 601, 602])
        XCTAssertEqual(r.model.queue.map(\.sessionID), ["s9", "s9", "s9"])
        XCTAssertEqual(r.model.queue.map(\.queueOrigin), [.explicit, .auto, .auto])
        XCTAssertEqual(r.model.queue.compactMap(\.upNextSeq), [1, 2])
        XCTAssertEqual(r.model.queue.compactMap(\.reasonLabel), ["same artist", "same artist"])
        XCTAssertEqual(r.upNext.state?.haveThrough, 2)
    }

    // MARK: the refill

    private func playing(_ r: UpNextRig, picks: [UpNextPick], mode: String = "library", have: Int? = nil) async {
        r.api.startAnswer = .success(UpNextSession(id: "s1", mode: mode, picks: picks, haveThroughSeq: have ?? (picks.last?.seq ?? 0)))
        r.model.playFromList([unTrack(501, "Veldrassen Overture")], at: 0, context: nil)
        await r.drain()
    }

    func testRefillsAtFiveLeftWithTheHighestSeqHeld() async {
        let r = UpNextRig()
        r.api.nextAnswers = [.success(UpNextBatch(picks: [unPick(4, 604)]))]
        await playing(r, picks: [unPick(1, 601), unPick(2, 602), unPick(3, 603)])
        let next = r.api.calls.filter { $0.what == "next" }
        XCTAssertEqual(next.first?.body, #"{"have_through_seq":3}"#, "three left after the song: asked at once")
        XCTAssertEqual(r.model.queue.compactMap(\.upNextSeq), [1, 2, 3, 4])
        XCTAssertEqual(r.upNext.state?.haveThrough, 4)
    }

    func testARefillDropsWhatTheSessionTookBackAndNeverAddsWhatItHolds() async {
        let r = UpNextRig()
        r.api.nextAnswers = [.success(UpNextBatch(picks: [unPick(2, 602), unPick(3, 603)], removed: [1, 3]))]
        await playing(r, picks: [unPick(1, 601), unPick(2, 602)])
        XCTAssertEqual(r.model.queue.compactMap(\.upNextSeq), [2], "1 taken back, 2 held already, 3 taken back")
        XCTAssertEqual(r.upNext.state?.haveThrough, 3)
    }

    func testAFailedRefillSaysSoAndRetriesWithTheSameNumberOnTheNextSong() async {
        let r = UpNextRig()
        r.api.nextAnswers = [.failure(unFailure(503)), .success(UpNextBatch(picks: [unPick(3, 603)]))]
        await playing(r, picks: [unPick(1, 601), unPick(2, 602)])
        XCTAssertEqual(r.upNext.state?.offline, true)
        XCTAssertEqual(phoneUpNextLine(r.upNext.state, autoplayOn: true, waiting: 2), PhoneUpNextStrings.offline)
        XCTAssertEqual(r.api.count("next"), 1, "never in a loop")
        r.upNext.check()
        await r.drain()
        XCTAssertEqual(r.api.count("next"), 1, "not again on the same song")
        r.model.next()
        await r.drain()
        let bodies = r.api.calls.filter { $0.what == "next" }.map(\.body)
        XCTAssertEqual(Array(bodies.prefix(2)), [#"{"have_through_seq":2}"#, #"{"have_through_seq":2}"#],
                       "the same number again after a failure")
        XCTAssertEqual(r.upNext.state?.offline, false)
    }

    func testAnEmptyAnswerIsAskedAgainOnlyOnTheNextSong() async {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601), unPick(2, 602)])
        XCTAssertEqual(r.api.count("next"), 1)
        r.upNext.check()
        await r.drain()
        XCTAssertEqual(r.api.count("next"), 1)
        r.model.next()
        await r.drain()
        XCTAssertEqual(r.api.count("next"), 2)
    }

    func testAnEndedSessionIsNotAskedAgain() async {
        let r = UpNextRig()
        r.api.nextAnswers = [.success(UpNextBatch(ended: true))]
        await playing(r, picks: [unPick(1, 601)])
        r.model.next()
        await r.drain()
        XCTAssertEqual(r.api.count("next"), 1)
        XCTAssertEqual(r.upNext.state?.ended, true)
    }

    func testAutoplayOffAsksNothingMoreInALibraryMixButAListPlaysOut() async {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601)])
        try? await r.upNext.setAutoplay(false)
        await r.drain()
        let before = r.api.count("next")
        r.model.enqueue([unTrack(505, "b")])
        await r.drain()
        XCTAssertEqual(r.api.count("next"), before, "a mix with autoplay off asks nothing more")

        let l = UpNextRig()
        l.api.startAnswer = .success(UpNextSession(id: "s2", mode: "list", autoplay: false, picks: [unPick(1, 601)], haveThroughSeq: 1))
        l.model.playFromList(list, at: 0, context: UpNextContext(kind: "playlist", ref: "41", name: "L"))
        await l.drain()
        XCTAssertEqual(l.api.count("next"), 1, "a list plays out")
    }

    func testAListThatPlayedOutTurnsIntoContinue() async {
        let r = UpNextRig()
        r.api.nextAnswers = [.success(UpNextBatch(picks: [unPick(2, 602)], mode: "continue"))]
        await playing(r, picks: [unPick(1, 601)], mode: "list")
        XCTAssertEqual(r.upNext.state?.mode, "continue")
    }

    func testNoRefillWhileCasting() async {
        let r = UpNextRig()
        // Seven picks: more than five left, so nothing is asked yet.
        await playing(r, picks: (1...7).map { unPick($0, Int64(600 + $0)) })
        XCTAssertEqual(r.api.count("next"), 0)
        _ = await r.model.cast(to: "office")
        XCTAssertEqual(r.model.target, .room("office"))
        // Two out: five left, which would refill here — but the room refills
        // itself.
        r.model.remove(at: 7)
        r.model.remove(at: 6)
        await r.drain()
        XCTAssertEqual(r.api.count("next"), 0, "no local refill while casting")
    }

    // MARK: failures, late answers and other servers

    func testARefusedStartSaysNothingAndShowsNothing() async {
        let r = UpNextRig()
        r.api.startAnswer = .failure(unFailure(404))
        r.model.playFromList([unTrack(501, "a")], at: 0, context: nil)
        await r.drain()
        XCTAssertEqual(r.upNext.state?.refused, true)
        XCTAssertFalse(phoneUpNextShown(paired: true, hasLibrary: true, state: r.upNext.state, casting: false))
        r.model.next()
        await r.drain()
        XCTAssertEqual(r.api.count("start"), 1, "a refused play is never asked again")
    }

    func testAStartTheServerFailedIsSaidAndTriedAgainOnTheNextSong() async {
        let r = UpNextRig()
        r.api.startAnswer = .failure(unFailure(502))
        r.model.playList(list, context: nil)
        await r.drain()
        XCTAssertEqual(r.upNext.state?.offline, true)
        XCTAssertEqual(phoneUpNextLine(r.upNext.state, autoplayOn: true, waiting: 0), PhoneUpNextStrings.offline)
        r.upNext.check()
        await r.drain()
        XCTAssertEqual(r.api.count("start"), 1, "marked on this song, not tried again on it")
        r.api.startAnswer = .success(UpNextSession(id: "s3", mode: "continue"))
        r.model.next()
        await r.drain()
        XCTAssertEqual(r.api.count("start"), 2, "tried again on the next song")
        XCTAssertEqual(r.upNext.state?.id, "s3")
        XCTAssertEqual(r.model.queue.map(\.sessionID), ["s3", "s3", "s3"])
    }

    func testANetworkFailureIsOfflineToo() async {
        let r = UpNextRig()
        r.api.startAnswer = .failure(.unreachable("no route"))
        r.model.playFromList([unTrack(501, "a")], at: 0, context: nil)
        await r.drain()
        XCTAssertEqual(r.upNext.state?.offline, true)
        XCTAssertNotEqual(r.upNext.state?.refused, true)
    }

    func testANewerPlayWinsOverALateAnswer() async {
        let r = UpNextRig()
        r.api.startAnswer = .success(UpNextSession(id: "old", picks: [unPick(1, 601)], haveThroughSeq: 1))
        r.api.during = { [unowned r] in r.model.play([PlayerSeed.station]) }
        r.model.playFromList([unTrack(501, "a")], at: 0, context: nil)
        await r.drain()
        XCTAssertNil(r.upNext.state, "the older play's answer is dropped")
        XCTAssertFalse(r.model.queue.contains { $0.queueOrigin == .auto })
    }

    func testAnAnswerFromTheServerLeftIsDropped() async {
        let r = UpNextRig()
        r.api.startAnswer = .success(UpNextSession(id: "a", picks: [unPick(1, 601)], haveThroughSeq: 1))
        r.api.during = { [unowned r] in r.world.server = "http://10.0.0.43:6369" }
        r.model.playFromList([unTrack(501, "a")], at: 0, context: nil)
        await r.drain()
        XCTAssertNotEqual(r.upNext.state?.id, "a", "server A's session never lands under server B")
        XCTAssertEqual(r.model.queue.map(\.itemID), [501])
    }

    func testARefillAnsweredAfterANewPlayAddsNothing() async {
        let r = UpNextRig()
        r.api.startAnswer = .success(UpNextSession(id: "s1", picks: [unPick(1, 601), unPick(2, 602)], haveThroughSeq: 2))
        r.api.nextAnswers = [.success(UpNextBatch(picks: [unPick(3, 603)]))]
        // The new play lands inside the refill's round trip.
        r.api.duringNext = { [unowned r] in r.model.play([PlayerSeed.station]) }
        r.model.playFromList([unTrack(501, "a")], at: 0, context: nil)
        await r.drain()
        XCTAssertEqual(r.api.count("next"), 1, "the refill did go out")
        XCTAssertFalse(r.model.queue.contains { $0.upNextSeq == 3 }, "a refill for an older play adds nothing")
        XCTAssertEqual(r.model.queue.map(\.kind), [.radio])
    }

    // MARK: what the person does

    func testAutoplayOffDropsTheWaitingPicksOfAMixAndTellsTheServer() async throws {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601), unPick(2, 602)])
        try await r.upNext.setAutoplay(false)
        await r.drain()
        XCTAssertFalse(r.model.queue.contains { $0.queueOrigin == .auto })
        XCTAssertEqual(r.api.bodies("patch").last?["autoplay"] as? Bool, false)
        XCTAssertFalse(r.world.autoplay, "remembered for this device")
    }

    func testAutoplayOffKeepsAListsOwnPicks() async throws {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601), unPick(2, 602)], mode: "list")
        try await r.upNext.setAutoplay(false)
        XCTAssertEqual(r.model.queue.filter { $0.queueOrigin == .auto }.count, 2)
    }

    func testLessLikeThisDropsTheArtistsPicks() async throws {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601), unPick(2, 602), unPick(3, 603, artist: "Marrow Choir")])
        r.api.feedbackAnswer = .success([2])
        let gone = try await r.upNext.lessLikeThis(1)
        XCTAssertEqual(gone, [1, 2])
        XCTAssertEqual(r.model.queue.compactMap(\.upNextSeq), [3])
        XCTAssertEqual(r.api.bodies("feedback").last?["kind"] as? String, "less_like_this")
    }

    func testLessLikeThisOnASongThatIsNoPickSendsNothing() async throws {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601)])
        let gone = try await r.upNext.lessLikeThis(0)
        XCTAssertTrue(gone.isEmpty)
        XCTAssertEqual(r.api.count("feedback"), 0)
    }

    func testARemovedPickIsToldAndWhatItTakesBackLeaves() async {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601), unPick(2, 602), unPick(3, 603)])
        r.api.feedbackAnswer = .success([1, 3])
        r.model.remove(at: 1)
        await r.drain()
        XCTAssertEqual(r.api.bodies("feedback").last?["kind"] as? String, "remove")
        XCTAssertEqual(r.api.bodies("feedback").last?["seq"] as? Int, 1)
        XCTAssertEqual(r.model.queue.compactMap(\.upNextSeq), [2])
    }

    func testTheForSelectorPatchesTheListener() async throws {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601)])
        r.api.patchAnswer = .success(UpNextSession(id: "s1", person: UpNextPerson(id: 7, name: "Priya")))
        try await r.upNext.setListener(7)
        XCTAssertEqual(r.api.calls.last?.body, #"{"listener_person_id":7}"#)
        XCTAssertEqual(r.upNext.state?.person?.name, "Priya")
        XCTAssertEqual(r.world.listenerSet.last, "7")
        try await r.upNext.setListener(nil)
        XCTAssertEqual(r.api.calls.last?.body, #"{"listener_person_id":null}"#, "the household: present and null")
    }

    // MARK: casting

    func testACastCarriesTheSessionAutoplayAndListener() async {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601)])
        r.world.autoplay = false
        r.world.pick = "7"
        let fields = r.upNext.castFields()
        XCTAssertEqual(fields["up_next_session_id"] as? String, "s1")
        XCTAssertEqual(fields["autoplay"] as? Bool, false)
        XCTAssertEqual(fields["listener_person_id"] as? Int64, 7)
        _ = await r.model.cast(to: "office")
        let body = PlayerHouse.bodies(for: "/api/music/play-tracks").last
        XCTAssertEqual(body?["up_next_session_id"] as? String, "s1", "the session goes with the queue")
    }

    func testAnEndedSessionIsNotCast() async {
        let r = UpNextRig()
        r.api.nextAnswers = [.success(UpNextBatch(ended: true))]
        await playing(r, picks: [unPick(1, 601)])
        XCTAssertNil(r.upNext.castFields()["up_next_session_id"])
        XCTAssertNil(r.upNext.castFields()["autoplay"], "on is the server's default")
    }

    func testHandBackMovesTheSessionToThisDevice() async {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601)])
        _ = await r.model.cast(to: "office")
        _ = await r.model.cast(to: nil)
        await r.drain()
        XCTAssertEqual(r.api.count("move"), 1)
        XCTAssertEqual(r.api.calls.first { $0.what == "move" }?.body, #"{"device_id":"ios-phone-1","kind":"device"}"#)
        XCTAssertEqual(r.upNext.state?.id, "s1")
    }

    func testAHandBackTheRoomRefusedDropsTheSession() async {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601)])
        _ = await r.model.cast(to: "office")
        r.api.moveAnswer = .failure(unFailure(409))
        r.api.startAnswer = .success(UpNextSession(id: "s2", mode: "continue"))
        _ = await r.model.cast(to: nil)
        await r.drain()
        XCTAssertEqual(r.api.count("start"), 2, "dropped here, and the queue starts a new one near its end")
        XCTAssertEqual(r.upNext.state?.id, "s2", "the room's session was replaced: dropped here")
    }

    // MARK: back on screen

    func testARereadReplacesTheWaitingPicksWithTheServers() async {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601), unPick(2, 602)])
        r.api.readAnswer = .success(UpNextSession(id: "s1", mode: "library", picks: [unPick(2, 602), unPick(4, 604)], haveThroughSeq: 4))
        r.upNext.reread()
        await r.drain()
        XCTAssertEqual(r.model.queue.compactMap(\.upNextSeq), [2, 4])
        XCTAssertEqual(r.upNext.state?.haveThrough, 4)
    }

    func testASessionTheServerForgotIsDroppedOnReread() async {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601), unPick(2, 602)])
        r.api.readAnswer = .failure(unFailure(404))
        r.api.startAnswer = .success(UpNextSession(id: "s2", mode: "continue"))
        r.upNext.reread()
        await r.drain()
        XCTAssertEqual(r.upNext.state?.id, "s2", "the forgotten session is dropped; the queue starts its own")
    }

    // MARK: the queue

    func testAddToQueueGoesBeforeTheWaitingPicksAndPlayNextRightAfterTheSong() async {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601), unPick(2, 602)])
        r.model.enqueue([unTrack(505, "Sablewind Figure")])
        XCTAssertEqual(r.model.queue.map(\.itemID).prefix(4), [501, 505, 601, 602])
        r.model.playNext([unTrack(506, "Harrowgate Lantern")])
        XCTAssertEqual(r.model.queue.map(\.itemID).prefix(5), [501, 506, 505, 601, 602])
        let origins = UpNextRules.explicitRows(r.model.queue.map(\.queueOrigin), index: r.model.index)
        XCTAssertEqual(origins.count, 3, "the count leaves the waiting picks out")
    }

    func testANewPlayReplacesTheSessionAndWhatItPlaysWasAskedFor() async {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601)])
        guard let old = r.model.queue.last else { return XCTFail("no pick queued") }
        r.api.startAnswer = .failure(unFailure(404))
        r.model.play([old, unTrack(505, "b")])
        XCTAssertEqual(r.model.queue.map(\.queueOrigin), [.explicit, .explicit])
        XCTAssertEqual(r.model.queue.compactMap(\.sessionID), [])
    }

    func testClearingTheQueueDropsTheSession() async {
        let r = UpNextRig()
        await playing(r, picks: [unPick(1, 601)])
        r.model.clear()
        XCTAssertNil(r.upNext.state)
    }

    // MARK: the rules and the words

    func testAddAtLandsBeforeTheFirstPickStillToCome() {
        var a = unTrack(1, "a"); a.queueOrigin = .auto
        var b = unTrack(2, "b"); b.queueOrigin = .auto
        let q = [a, unTrack(3, "c"), b, unTrack(4, "d")]
        XCTAssertEqual(UpNextRules.addAt(q, index: 0), 2, "the playing pick is not one still to come")
        XCTAssertEqual(UpNextRules.addAt([unTrack(1, "a")], index: 0), 1)
    }

    func testTheSectionIsHiddenWhereItCantWork() {
        XCTAssertTrue(phoneUpNextShown(paired: true, hasLibrary: true, state: nil, casting: false))
        XCTAssertFalse(phoneUpNextShown(paired: false, hasLibrary: true, state: nil, casting: false))
        XCTAssertFalse(phoneUpNextShown(paired: true, hasLibrary: false, state: nil, casting: false))
        XCTAssertFalse(phoneUpNextShown(paired: true, hasLibrary: true, state: nil, casting: true))
        let refused = UpNextState(server: "s", gen: 1, refused: true)
        XCTAssertFalse(phoneUpNextShown(paired: true, hasLibrary: true, state: refused, casting: false))
        XCTAssertFalse(phoneUpNextForShown(people: 1, state: UpNextState(server: "s", gen: 1, id: "x")))
        XCTAssertFalse(phoneUpNextForShown(people: 2, state: nil))
        XCTAssertTrue(phoneUpNextForShown(people: 2, state: UpNextState(server: "s", gen: 1, id: "x")))
    }

    func testTheMutedLine() {
        let list = UpNextState(server: "s", gen: 1, id: "x", mode: "list",
                               seed: RoomUpNextSeed(list: RoomUpNextList(kind: "playlist", name: "Longwater Drive")))
        XCTAssertEqual(phoneUpNextLine(list, autoplayOn: false, waiting: 2),
                       "autoplay is off — stops after the last song of Longwater Drive")
        XCTAssertEqual(phoneUpNextLine(list, autoplayOn: false, waiting: 0),
                       "autoplay is off — the queue stops at the end")
        XCTAssertEqual(phoneUpNextLine(UpNextState(server: "s", gen: 1, loading: true), autoplayOn: true, waiting: 0),
                       "finding songs like this one…")
        XCTAssertEqual(phoneUpNextLine(nil, autoplayOn: true, waiting: 0),
                       "similar songs join here when the queue is nearly over")
        XCTAssertNil(phoneUpNextLine(list, autoplayOn: true, waiting: 1))
        XCTAssertEqual(PhoneUpNextStrings.autoplayLabel, "autoplay: keep playing similar songs when the queue ends")
        XCTAssertEqual(PhoneUpNextStrings.offline, "up next needs the home server")
    }

    func testDecodesTheServersSessionAndBatch() throws {
        let session = try JSONDecoder().decode(UpNextSession.self, from: Data("""
        {"id":"u-1","target":{"kind":"device","device_id":"ios-1"},"mode":"list","autoplay":false,
         "person":{"id":7,"name":"Priya"},
         "seed":{"track_id":501,"title":"Veldrassen Overture","artist":"Orbis Quill","label":null,
                 "list":{"kind":"playlist","name":"Longwater Drive"}},
         "picks":[{"seq":1,"track_id":503,"title":"Thrumming Ashkeep","artist":"Nidra Vane","album":"",
                   "duration_sec":262.0,"reason_code":"same_artist","reason_label":"same artist",
                   "status":"queued","mpd_song_id":null,"batch_no":1}],
         "batch_no":1,"have_through_seq":1,"ended":false}
        """.utf8))
        XCTAssertEqual(session.id, "u-1")
        XCTAssertEqual(session.target?.deviceID, "ios-1")
        XCTAssertFalse(session.autoplay)
        XCTAssertEqual(session.person?.name, "Priya")
        XCTAssertEqual(session.seed?.list?.name, "Longwater Drive")
        XCTAssertEqual(session.picks.first?.reasonLabel, "same artist")
        XCTAssertEqual(session.haveThroughSeq, 1)
        let batch = try JSONDecoder().decode(UpNextBatch.self, from: Data(
            #"{"batch_no":2,"picks":[],"ended":true,"removed":[3,4],"mode":"continue"}"#.utf8))
        XCTAssertTrue(batch.ended)
        XCTAssertEqual(batch.removed, [3, 4])
        XCTAssertEqual(batch.mode, "continue")
    }

    func testAPickIsAQueueItemThatSaysWhy() {
        let item = UpNextRules.pickItem(UpNextPick(seq: 4, trackID: 604, title: " ", artist: "", reasonLabel: ""), sessionID: "s")
        XCTAssertEqual(item.title, "track")
        XCTAssertNil(item.artist)
        XCTAssertNil(item.reasonLabel)
        XCTAssertEqual(item.queueOrigin, .auto)
        XCTAssertEqual(item.src, "/api/music/library/604/audio")
    }

    func testTheAutoplayPreferenceIsOnUntilSwitchedOff() {
        let place = SavedStateBox()
        let store = UpNextAutoplayStore(place: place)
        XCTAssertTrue(store.on)
        store.set(false)
        XCTAssertFalse(UpNextAutoplayStore(place: place).on, "remembered")
        store.set(true)
        XCTAssertTrue(UpNextAutoplayStore(place: place).on)
    }

    func testARoomPlaylistCarriesThisPhonesAutoplayOff() throws {
        let off = try JSONSerialization.jsonObject(with: RoomPlayBodies.playPlaylist(
            room: "parlour", playlistID: 41, shuffle: false, deviceID: "d", listener: .unnamed, autoplay: false
        )) as? [String: Any]
        XCTAssertEqual(off?["autoplay"] as? Bool, false)
        let on = try JSONSerialization.jsonObject(with: RoomPlayBodies.playPlaylist(
            room: "parlour", playlistID: 41, shuffle: false, deviceID: "d", listener: .unnamed
        )) as? [String: Any]
        XCTAssertNil(on?["autoplay"], "on is the server's default and left out")
    }
}

// MARK: - listening history carries the session

@MainActor
final class ListenUpNextTests: XCTestCase {

    private var mono: Int64 = 0
    private var dir: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("listenupnext-\(UUID().uuidString)")
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: dir)
        super.tearDown()
    }

    private func rig() -> (ListenOutbox, ListenLog) {
        let o = ListenOutbox(file: dir.appendingPathComponent("o.json"))
        let l = ListenLog(
            outbox: o,
            tracker: ListenTracker(wallMs: { 1_762_970_591_000 }, monoMs: { [unowned self] in self.mono }),
            sender: nil,
            server: { "http://10.0.0.5:6369" },
            deviceID: { "ios-1a2b" },
            pickFor: { _ in nil },
            sharedAt: { _ in false },
            io: { $0() }
        )
        return (o, l)
    }

    private func wire(_ row: OutboxRow?) -> [String: Any]? {
        guard let row, let data = try? JSONEncoder().encode(row.wire) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    func testAPickSaysAutoAndItsSession() {
        let (o, l) = rig()
        let item = UpNextRules.pickItem(UpNextPick(seq: 2, trackID: 602, title: "p", durationSec: 200), sessionID: "s7")
        l.observe(ListenSnapshot(item: item, playing: true, local: true))
        mono += 30_000
        l.skip()
        let w = wire(o.all().first)
        XCTAssertEqual(w?["origin"] as? String, "auto")
        XCTAssertEqual(w?["session_id"] as? String, "s7")
    }

    func testAWholeListsSongSaysList() {
        let (o, l) = rig()
        var item = unTrack(501, "a")
        item.queueOrigin = .wholeList
        l.observe(ListenSnapshot(item: item, playing: true, local: true))
        mono += 30_000
        l.stopped()
        XCTAssertEqual(wire(o.all().first)?["origin"] as? String, "list")
    }

    func testTheSessionThatCameBackAfterTheSongBeganIsOnItsListen() {
        let (o, l) = rig()
        let song = unTrack(501, "a")
        l.observe(ListenSnapshot(item: song, playing: true, local: true))
        mono += 10_000
        var tagged = song
        tagged.sessionID = "late"
        l.observe(ListenSnapshot(item: tagged, playing: true, local: true))
        mono += 10_000
        l.stopped()
        XCTAssertEqual(o.all().count, 1, "the same listen, not a second one")
        XCTAssertEqual(wire(o.all().first)?["session_id"] as? String, "late")
    }

    func testASongOfNoSessionSendsNoSessionAndExplicit() {
        let (o, l) = rig()
        l.observe(ListenSnapshot(item: unTrack(501, "a"), playing: true, local: true))
        mono += 10_000
        l.stopped()
        let w = wire(o.all().first)
        XCTAssertNil(w?["session_id"])
        XCTAssertEqual(w?["origin"] as? String, "explicit")
    }

    func testAnOutboxRowFromBeforeUpNextStillReads() throws {
        let old = #"{"id":"x","server":"s","track_id":1,"device_id":"d","started_at":"2025-01-01T00:00:00Z","played_sec":5,"outcome":"stopped","as_household":false}"#
        let row = try JSONDecoder().decode(OutboxRow.self, from: Data(old.utf8))
        XCTAssertNil(row.sessionID)
        XCTAssertEqual(row.origin, "explicit")
    }

    /// The player itself: the start's answer puts the session on the song
    /// already playing, and its listen carries it.
    func testThePlayersSongCarriesTheSessionThatArrivedLate() async {
        StubURLProtocol.reset()
        PlayerHouse().install()
        defer { StubURLProtocol.reset() }
        let (o, l) = rig()
        let r = UpNextRig()
        r.model.listens = l
        r.api.startAnswer = .success(UpNextSession(id: "s5", mode: "library"))
        r.model.playFromList([unTrack(501, "a")], at: 0, context: nil)
        await r.drain()
        mono += 20_000
        r.model.clear()
        XCTAssertEqual(wire(o.all().first)?["session_id"] as? String, "s5")
    }
}
