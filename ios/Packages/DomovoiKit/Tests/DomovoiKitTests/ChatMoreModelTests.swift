import Foundation
import XCTest
@testable import DomovoiKit

/// The rest of Chat's model against a server that is not a server: the second
/// read that carries the extras, the two cards being answered, the chip, a
/// thread's jobs, a spoken turn, and the thread switched **inside** a round
/// trip.
///
/// `ConversationPane` is a composable, so nothing on Android specifies these
/// decisions: which card may be answered, which row offers "carry on",
/// whether the chip can be tapped, what a refused turn says and which thread
/// an answer belongs to are tested here for the first time on either phone.
@MainActor
final class ChatMoreModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// A round trip held open on the stub's own thread, so that a test can do
    /// something **between the request leaving and the answer landing**.
    ///
    /// The shape `FilesModelTests`'s gate has, with one difference: this one
    /// waits on the URL loading thread rather than in an injected sleeper,
    /// because what has to be suspended here is the server and not the model.
    /// Switching threads "right after `talk(...)` returns" would only prove
    /// that a task which has not started yet writes nothing.
    private final class Gate: Sendable {
        /// The request has reached the server.
        let arrived = Shared<Bool>(false)
        private let opened = Shared<Bool>(false)

        func release() { opened.set(true) }

        /// Called on the stub's thread. Bounded, so a test that forgets to
        /// release it fails instead of hanging for ever.
        func hold() {
            arrived.set(true)
            for _ in 0..<5_000 {
                if opened.current { return }
                Thread.sleep(forTimeInterval: 0.001)
            }
        }
    }

    /// A house with two threads: 7, with one of everything on it, and 8, with
    /// a stopped job.
    private final class House: Sendable {

        static let sevenRows = """
        {"messages":[
          {"id":10,"role":"user","content":"what does the lease say about pets?",
           "created_at":"2026-11-02T17:59:00+00:00","source":"voice","speaker_person_id":4,
           "speaker_name":"Ana","speaker_confident":true,
           "images":[{"token":"p1","name":"kitchen.jpg"},{"token":"p2","name":"hall.jpg"}],
           "documents":[{"token":"a1","name":"lease.pdf","pages":9,"parts":8}]},
          {"id":11,"role":"assistant","content":"Cats are allowed with written consent.",
           "created_at":"2026-11-02T18:00:00+00:00","model":"llama3.2:latest"},
          {"id":12,"role":"assistant","content":"May I look this up online?",
           "stats":{"model_role":"lookup","lookup_card":{"id":12,"kind":"search",
                    "query":"pet clause","destination":"Wikipedia","state":"pending"}}},
          {"id":13,"role":"assistant","content":"this is a long document",
           "doc":{"mode":"heads_up","found":true,"heads_up":{"kind":"long","text":"This will take a while.",
                  "options":[{"id":"full","label":"read all of it"},{"id":"skim","label":"skim it"},
                             {"id":"choose_another","label":"choose another picture"}]}}}
        ],"jobs":[]}
        """

        static let eightRows = """
        {"messages":[
          {"id":20,"role":"user","content":"summarise the lease"},
          {"id":21,"role":"assistant","content":"part 1 of 8","doc":{"mode":"answer","job":{"id":"j7"}}},
          {"id":31,"role":"assistant","content":"part 3 of 8","doc":{"mode":"answer","job":{"id":"j7"}}}
        ],
        "jobs":[{"id":"j7","state":"stopped","part":4,"parts":8,"read":3,
                 "line":"stopped at part 3 of 8","thread_id":8}]}
        """

        let rows = Shared<[Int64: String]>([7: House.sevenRows, 8: House.eightRows])
        let internet = Shared<String>(#"{"internet":"ask","always_allow":false,"lookups":true}"#)
        let internetStatus = Shared<Int>(200)

        let cardStream = Shared<String>("""
        event: delta
        data: {"text":"Cats are "}

        event: lookup_permission
        data: {"id":12,"kind":"search"}

        event: delta
        data: {"text":"allowed."}

        event: done
        data: {"id":14,"role":"assistant","content":"Cats are allowed."}

        """)
        let cardStatus = Shared<Int>(200)

        let jobBody = Shared<String>(
            #"{"id":"j7","state":"running","part":4,"parts":8,"read":3,"line":"reading part 4 of 8"}"#
        )
        let jobStatus = Shared<Int>(200)
        let jobDetail = Shared<String>(#"{"detail":"that job has already finished"}"#)

        let jobStream = Shared<String>("""
        event: progress
        data: {"id":"j7","state":"running","part":6,"parts":8,"read":5,"line":"reading part 6 of 8"}

        event: done
        data: {"id":"j7","state":"done","part":8,"parts":8,"read":8,"line":"read all 8 parts"}

        """)

        let patchStatus = Shared<Int>(200)

        let voiceBody = Shared<String>("")
        let voiceStatus = Shared<Int>(200)

        /// Held round trips, by the path that should wait.
        let gates = Shared<[String: Gate]>([:])

        /// "METHOD path", in order.
        let seen = Shared<[String]>([])

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url,
                      let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let method = request.httpMethod ?? "GET"
                let path = parts.path
                if path != "/api/health" {
                    self.seen.mutate { $0.append("\(method) \(path)") }
                }
                if let gate = self.gates.current[path] { gate.hold() }

                func sse(_ text: String) -> StubURLProtocol.Answer {
                    .json(text, headers: ["Content-Type": "text/event-stream"])
                }

                switch (method, path) {
                case ("GET", "/api/health"):
                    return .json((try? TestIdentity.real.healthBody(
                        challenge: parts.queryItems?.first { $0.name == "challenge" }?.value
                    )) ?? "{}")
                case ("GET", ChatPath.internet):
                    guard self.internetStatus.current == 200 else {
                        return .json(#"{"detail":"no"}"#, status: self.internetStatus.current)
                    }
                    return .json(self.internet.current)
                default:
                    break
                }
                if method == "GET", path.hasSuffix("/messages") {
                    let id = Int64(path.split(separator: "/").dropLast().last.map(String.init) ?? "") ?? 0
                    return .json(self.rows.current[id] ?? #"{"messages":[],"jobs":[]}"#)
                }
                if method == "GET", path.hasPrefix("/api/chat/uploads/") {
                    return .json(String(repeating: "x", count: 200))
                }
                if method == "POST", path.hasSuffix("/voice") {
                    guard self.voiceStatus.current == 200 else {
                        return .json(
                            #"{"detail":"a voice turn is at most 30 seconds","code":"clip_too_long"}"#,
                            status: self.voiceStatus.current
                        )
                    }
                    return .json(self.voiceBody.current)
                }
                if method == "GET", path.hasSuffix("/events") {
                    return sse(self.jobStream.current)
                }
                if method == "POST", path.hasPrefix("/api/chat/doc-jobs/") {
                    guard self.jobStatus.current == 200 else {
                        return .json(self.jobDetail.current, status: self.jobStatus.current)
                    }
                    return .json(self.jobBody.current)
                }
                if method == "POST", path.contains("/lookups/") || path.hasSuffix("/heads-up") {
                    guard self.cardStatus.current == 200 else {
                        return .json(
                            #"{"detail":"AI features are turned off for this home"}"#,
                            status: self.cardStatus.current
                        )
                    }
                    return sse(self.cardStream.current)
                }
                if method == "PATCH", path.hasPrefix("/api/chat/threads/") {
                    guard self.patchStatus.current == 200 else {
                        return .json(#"{"detail":"nope"}"#, status: self.patchStatus.current)
                    }
                    return .json(#"{"ok":true}"#)
                }
                return .json("{}", status: 404)
            }
        }
    }

    private func rig(
        house: House = House(),
        headsUp: ChatMoreHeadsUpStore = ChatMoreHeadsUpStore(place: SavedStateBox())
    ) -> (ChatMoreModel, House, Shared<Int>) {
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
        let reopened = Shared<Int>(0)
        let model = ChatMoreModel(
            in: container,
            deviceID: "ios-1",
            headsUp: headsUp,
            now: { Date(timeIntervalSince1970: 1_762_110_000) },
            reopen: { reopened.mutate { $0 += 1 } }
        )
        return (model, house, reopened)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the model",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func everythingLands() async {
        for _ in 0..<settleYieldLimit { await Task.yield() }
    }

    private func body(of path: String, method: String = "POST") throws -> [String: Any] {
        let request = try XCTUnwrap(
            StubURLProtocol.recorded.last { $0.httpMethod == method && $0.url?.path == path },
            "no \(method) \(path) was made"
        )
        let data = try XCTUnwrap(request.httpBody, "\(method) \(path) carried no body")
        return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    private func open(_ model: ChatMoreModel, _ id: Int64, internetAllowed: Bool = false) async {
        model.open(thread: id, internetAllowed: internetAllowed)
        await settleUntil({ !model.detail.rows.isEmpty }, "thread \(id)'s extras")
    }

    // MARK: - the second read

    func testTheExtrasAndTheJobsLandKeyedByMessage() async {
        let (model, _, _) = rig()
        await open(model, 7)
        XCTAssertEqual(model.detail.rows.count, 4)
        XCTAssertEqual(model.extras(for: 10)?.documents.first?.name, "lease.pdf")
        XCTAssertEqual(model.extras(for: 10)?.images.count, 2)
        XCTAssertEqual(model.lookupCard(for: 12)?.query, "pet clause")
        XCTAssertNotNil(model.extras(for: 13)?.headsUp)
        // The speaker marker, from the three fields the transcript drops.
        XCTAssertEqual(model.speakerMark(for: 10)?.label, "Ana")
        XCTAssertNil(model.speakerMark(for: 11), "a reply has no speaker")

        await open(model, 8)
        XCTAssertEqual(model.jobs.map(\.id), ["j7"])
        XCTAssertEqual(model.jobs.first?.state, "stopped")
    }

    func testEveryPictureOfEveryRowIsAskedForOnce() async {
        let (model, house, _) = rig()
        await open(model, 7)
        await settleUntil({ model.pictures.picture("p2") != nil }, "the pictures")
        XCTAssertEqual(model.pictures.picture("p1"), .image(Data(repeating: 0x78, count: 200)))
        model.refresh()
        await everythingLands()
        XCTAssertEqual(
            house.seen.current.filter { $0 == "GET /api/chat/uploads/p1" }.count,
            1,
            "a picture already held is not fetched again"
        )
    }

    /// A failed extras read says **nothing**: the words of the conversation
    /// came from the same route through ``ChatModel``, and a second toast for
    /// one outage is the thing being avoided here.
    func testAFailedExtrasReadIsSilent() async {
        let (model, house, _) = rig()
        house.rows.set([:])
        model.open(thread: 7)
        await everythingLands()
        XCTAssertTrue(model.detail.rows.isEmpty)
        XCTAssertNil(model.toast)
    }

    // MARK: - the chip

    func testTheChipReadsTheHouseholdThenTheThread() async {
        let (model, _, _) = rig()
        model.open(thread: 7, internetAllowed: false)
        // Nothing at all until the household has answered: the chip does not
        // guess.
        XCTAssertNil(model.internetChip)
        await settleUntil({ model.internetChip != nil }, "the chip")
        XCTAssertEqual(model.internetChip?.label, "internet off")
        XCTAssertTrue(model.internetChipTappable)

        await open(model, 7, internetAllowed: true)
        await settleUntil({ model.internetChip?.on == true }, "the chip")
        XCTAssertEqual(model.internetChip?.label, "internet on for this chat")
    }

    func testTheChipCannotBeTappedWhenTheHouseholdHasAlreadyDecided() async {
        let (model, house, _) = rig()
        house.internet.set(#"{"internet":"never","always_allow":false}"#)
        await open(model, 7)
        await settleUntil({ model.internetChip != nil }, "the chip")
        XCTAssertFalse(model.internetChipTappable)
        XCTAssertFalse(model.setInternetAllowed(true), "a tap that cannot change anything is not sent")
        await everythingLands()
        XCTAssertTrue(house.seen.current.allSatisfy { !$0.hasPrefix("PATCH") })
        XCTAssertEqual(model.internetChipHint, ChatMoreStrings.chipHintNever)
    }

    /// A chip that flipped on a refused write would say this chat looks things
    /// up when it does not.
    func testTheFlagIsWrittenOnlyAfterTheServerAgrees() async {
        let (model, house, _) = rig()
        await open(model, 7)
        await settleUntil({ model.internetChip != nil }, "the chip")

        XCTAssertTrue(model.setInternetAllowed(true))
        await settleUntil({ model.internetAllowed }, "the flag")
        XCTAssertEqual(try? body(of: "/api/chat/threads/7", method: "PATCH")["internet_allowed"] as? Bool, true)

        house.patchStatus.set(503)
        XCTAssertTrue(model.setInternetAllowed(false))
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertTrue(model.internetAllowed, "a refused write leaves the flag as it was")
        XCTAssertTrue(model.toast?.contains(ChatMoreStrings.verbChangeIt) ?? false, model.toast ?? "nothing")
    }

    // MARK: - the lookup card

    func testAnsweringALookupStreamsTheReplyReadsBackAndTurnsTheChipOn() async {
        let (model, house, reopened) = rig()
        await open(model, 7)
        await settleUntil({ model.internetChip != nil }, "the chip")
        XCTAssertTrue(model.lookupAnswerable(12))

        XCTAssertTrue(model.decideLookup(message: 12, decision: "chat"))
        // The card shows its answer at once, before the stream says anything.
        XCTAssertEqual(model.lookupCard(for: 12)?.state, "allowed_chat")
        XCTAssertFalse(model.lookupAnswerable(12), "an answered card has no buttons")

        await settleUntil({ !model.answering }, "the stream")
        // An event this app does not use (`lookup_permission`) did not end it.
        XCTAssertEqual(model.cardReply, "Cats are allowed.")
        XCTAssertNil(model.cardError)
        XCTAssertTrue(model.internetAllowed, "\u{201C}in this chat\u{201D} sets the thread's own flag")
        XCTAssertEqual(reopened.current, 1, "the transcript is re-read once")
        XCTAssertEqual(
            house.seen.current.filter { $0 == "GET /api/chat/threads/7/messages" }.count, 2,
            "the extras are read again too"
        )
        let sent = try? body(of: "/api/chat/threads/7/lookups/12")
        XCTAssertEqual(sent?["decision"] as? String, "chat")
        XCTAssertEqual(sent?["device_id"] as? String, "ios-1")
    }

    func testDecliningALookupLeavesTheChipAlone() async {
        let (model, _, _) = rig()
        await open(model, 7)
        XCTAssertTrue(model.decideLookup(message: 12, decision: "no"))
        await settleUntil({ !model.answering }, "the stream")
        XCTAssertEqual(model.lookupCard(for: 12)?.state, "declined")
        XCTAssertFalse(model.internetAllowed)
    }

    func testOnlyOneCardIsAnsweredAtATime() async {
        let (model, house, _) = rig()
        let gate = Gate()
        house.gates.set(["/api/chat/threads/7/lookups/12": gate])
        await open(model, 7)

        XCTAssertTrue(model.decideLookup(message: 12, decision: "once"))
        await settleUntil({ gate.arrived.current }, "the lookup request")
        XCTAssertTrue(model.answering)
        XCTAssertFalse(model.pickHeadsUp(message: 13, option: "skim"), "nothing else answers meanwhile")
        XCTAssertFalse(model.headsUpAnswerable(13, isLastRow: true))
        gate.release()
        await settleUntil({ !model.answering }, "the stream")
        XCTAssertTrue(model.headsUpAnswerable(13, isLastRow: true))
    }

    func testARefusedCardAnswerSaysSoWhereTheReplyWouldBe() async {
        let (model, house, _) = rig()
        house.cardStatus.set(503)
        await open(model, 7)
        XCTAssertTrue(model.decideLookup(message: 12, decision: "once"))
        await settleUntil({ model.cardError != nil }, "the refusal")
        XCTAssertFalse(model.answering)
        XCTAssertTrue(
            model.cardError?.contains("AI features are turned off") ?? false,
            model.cardError ?? "nothing"
        )
    }

    // MARK: - the heads-up card

    func testAHeadsUpPickCarriesTheKindsThisPhoneHasSilenced() async {
        let (model, _, _) = rig()
        await open(model, 7)
        // A heads-up card is answerable only as the thread's last row: the
        // server 409s a pick on anything else.
        XCTAssertFalse(model.headsUpAnswerable(13, isLastRow: false))
        XCTAssertTrue(model.headsUpAnswerable(13, isLastRow: true))

        model.silenceHeadsUp(kind: "scanned")
        XCTAssertTrue(model.headsUpSilenced(kind: "scanned"))
        XCTAssertTrue(model.pickHeadsUp(message: 13, option: "skim"))
        XCTAssertEqual(model.headsUpChosen(for: 13), "skim")
        await settleUntil({ !model.answering }, "the stream")

        let sent = try? body(of: "/api/chat/threads/7/heads-up")
        XCTAssertEqual(sent?["message_id"] as? Int, 13)
        XCTAssertEqual(sent?["option"] as? String, "skim")
        XCTAssertEqual((sent?["heads_up"] as? [String: Any])?["skip"] as? [String], ["scanned"])
    }

    /// `choose_another` re-opens the picture picker, which this app has not
    /// got — so it is not offered and not sent either.
    func testTheOptionThisAppCannotCarryOutIsNeitherOfferedNorSent() async {
        let (model, house, _) = rig()
        await open(model, 7)
        XCTAssertEqual(model.extras(for: 13)?.headsUp?.offerable.map(\.id), ["full", "skim"])
        XCTAssertFalse(model.pickHeadsUp(message: 13, option: "choose_another"))
        XCTAssertFalse(model.pickHeadsUp(message: 13, option: ""))
        XCTAssertFalse(model.pickHeadsUp(message: 11, option: "skim"), "a row that is not a card")
        await everythingLands()
        XCTAssertTrue(house.seen.current.allSatisfy { !$0.hasSuffix("/heads-up") })
        XCTAssertNil(model.headsUpChosen(for: 13))
    }

    // MARK: - jobs

    func testCarryOnIsOfferedOnTheStoppedJobsNewestRowOnly() async {
        let (model, _, _) = rig()
        await open(model, 8)
        XCTAssertNil(model.carryOn(for: 21), "the job's older reply")
        let offer = model.carryOn(for: 31)
        XCTAssertEqual(offer?.job.id, "j7")
        // `part` is 4 and `read` is 3: a count read from `part` would promise
        // one part too few.
        XCTAssertEqual(offer?.left, 5)
        XCTAssertNil(model.carryOn(for: 20), "a question offers nothing")
    }

    func testCarryingOnPutsTheJobsNewViewBackInPlaceAndFollowsIt() async {
        let (model, house, _) = rig()
        await open(model, 8)
        model.resumeJob("j7")
        // The POST answers with the job running, and a running job is
        // followed — the stream then carries it to done.
        await settleUntil({ model.jobs.first?.state == "done" }, "the job's stream")
        XCTAssertEqual(model.jobs.count, 1, "the view replaces the job, it is not appended")
        XCTAssertEqual(model.jobs.first?.read, 8)
        XCTAssertTrue(house.seen.current.contains("POST /api/chat/doc-jobs/j7/resume"))
        XCTAssertTrue(house.seen.current.contains("GET /api/chat/doc-jobs/j7/events"))
        // A job that has finished needs no follower, and its reply is a row
        // now — so the thread is read again.
        await settleUntil(
            { house.seen.current.filter { $0 == "GET /api/chat/threads/8/messages" }.count >= 2 },
            "the re-read after the job"
        )
    }

    func testARefusedJobWriteShowsTheServersOwnSentence() async {
        let (model, house, _) = rig()
        house.jobStatus.set(409)
        await open(model, 8)
        model.stopJob("j7")
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual(model.toast, "that job has already finished")
        XCTAssertEqual(model.jobs.first?.state, "stopped", "nothing was changed")
    }

    // MARK: - a spoken turn

    private static func wav(seconds: Double = 0.5, rate: Int = 16_000) -> Data {
        func u32(_ value: UInt32) -> [UInt8] {
            [UInt8(value & 0xFF), UInt8((value >> 8) & 0xFF), UInt8((value >> 16) & 0xFF), UInt8((value >> 24) & 0xFF)]
        }
        func u16(_ value: Int) -> [UInt8] { [UInt8(value & 0xFF), UInt8((value >> 8) & 0xFF)] }
        let samples = Int(Double(rate) * seconds)
        let pcm = [UInt8](repeating: 0, count: samples * 2)
        var body: [UInt8] = Array("WAVE".utf8)
        body += Array("fmt ".utf8) + u32(16) + u16(1) + u16(1) + u32(UInt32(rate))
        body += u32(UInt32(rate * 2)) + u16(2) + u16(16)
        body += Array("data".utf8) + u32(UInt32(pcm.count)) + pcm
        return Data(Array("RIFF".utf8) + u32(UInt32(body.count)) + body)
    }

    private static func voiceAnswer(
        audio: Bool = true,
        followup: Bool = false,
        heard: Bool = true
    ) -> String {
        let clip = audio ? #"{"mime":"audio/wav","b64":"\#(wav().base64EncodedString())"}"# : "null"
        return """
        {"heard":\(heard),"transcript":"what does the lease say about pets?",
         "reply":"Cats are allowed with written consent.","audio":\(clip),
         "speaker":{"person_id":4,"name":"Ana","confident":true},
         "expect_followup":\(followup),
         "messages":[{"id":10,"role":"user","content":"what does the lease say about pets?","source":"voice"},
                     {"id":11,"role":"assistant","content":"Cats are allowed with written consent."}]}
        """
    }

    /// Four seconds of 16 kHz mono PCM16 — long enough for the core's floor.
    private static let clip = Data(repeating: 0, count: 16_000 * 2 * 4)

    func testASpokenTurnReadsBothRowsBackAndSpeaksWhatTheServerSent() async {
        let (model, house, reopened) = rig()
        house.voiceBody.set(Self.voiceAnswer(followup: true))
        await open(model, 7)

        XCTAssertTrue(model.pressTalk())
        XCTAssertEqual(model.talkPhase, .recording)
        XCTAssertEqual(model.talkHint, ChatMoreStrings.listening)
        XCTAssertTrue(model.talk(pcm: Self.clip))
        XCTAssertEqual(model.talkPhase, .sending)

        await settleUntil({ model.talkPhase == .speaking }, "the turn")
        XCTAssertNotNil(model.replyAudio, "the server's own WAV")
        XCTAssertTrue(model.followup)
        XCTAssertEqual(model.talkHint, ChatMoreStrings.pressToStop)
        XCTAssertFalse(model.answering)
        // The core wrote both rows itself, so they come from the re-read and
        // are not invented here.
        XCTAssertEqual(reopened.current, 1)
        XCTAssertTrue(house.seen.current.contains("POST /api/chat/threads/7/voice"))

        model.finishedSpeaking()
        XCTAssertEqual(model.talkPhase, .idle)
        XCTAssertNil(model.replyAudio)
        // Domovoi asked something back, so the button invites an answer.
        XCTAssertEqual(model.talkHint, ChatMoreStrings.askedYouSomething)
    }

    /// No audio from the server means **nothing is spoken** — the phase never
    /// enters `speaking`, so no control claims a voice that will not come.
    func testATurnWithNoAudioGoesQuietRatherThanClaimingAVoice() async {
        let (model, house, _) = rig()
        house.voiceBody.set(Self.voiceAnswer(audio: false))
        await open(model, 7)
        XCTAssertTrue(model.talk(pcm: Self.clip))
        await settleUntil({ model.talkPhase == .idle }, "the turn")
        XCTAssertNil(model.replyAudio)
    }

    func testATurnTooLongShowsTheCoresOwnSentence() async {
        let (model, house, _) = rig()
        house.voiceStatus.set(413)
        await open(model, 7)
        XCTAssertTrue(model.talk(pcm: Self.clip))
        await settleUntil({ model.talkNote != nil }, "the refusal")
        XCTAssertEqual(model.talkNote, "a voice turn is at most 30 seconds")
        XCTAssertNotEqual(model.talkNote, ChatMoreStrings.voiceTooLongFallback)
        XCTAssertEqual(model.talkPhase, .idle)
        XCTAssertFalse(model.answering)
        // And the note outlives the turn, where "hold to talk" would hide it.
        XCTAssertEqual(model.talkHint, "a voice turn is at most 30 seconds")
    }

    func testAClipTooShortIsNotSentAtAll() async {
        let (model, house, _) = rig()
        await open(model, 7)
        XCTAssertFalse(model.talk(pcm: Data(repeating: 0, count: 1_000)))
        XCTAssertEqual(model.talkNote, ChatMoreStrings.holdTheButtonWhileYouTalk)
        await everythingLands()
        XCTAssertTrue(house.seen.current.allSatisfy { !$0.hasSuffix("/voice") })
        // A press that was only there to stop a reply needs no advice.
        model.setToast(nil)
        XCTAssertFalse(model.talk(pcm: Data(repeating: 0, count: 10), interrupted: true))
        XCTAssertNil(model.talkNote)
    }

    func testTheHouseNotHeardSaysSoAndKeepsNothing() async {
        let (model, house, reopened) = rig()
        house.voiceBody.set(#"{"heard":false,"reason":"too_short"}"#)
        await open(model, 7)
        XCTAssertTrue(model.talk(pcm: Self.clip))
        await settleUntil({ model.talkNote != nil }, "the answer")
        XCTAssertEqual(model.talkNote, ChatMoreStrings.didntCatchThat)
        XCTAssertEqual(model.talkPhase, .idle)
        XCTAssertEqual(reopened.current, 0, "there is nothing to re-read")
    }

    // MARK: - the wrong-thread guard

    /// **[[read-after-await-wrong-key]].** The turn is spoken into thread 7;
    /// thread 8 is opened while the request is in the server's hands; the
    /// answer must write nothing — not the rows, not the audio, not the note,
    /// and not a re-read of the conversation now on screen.
    func testAThreadSwitchedInsideTheRoundTripWritesNothing() async {
        let (model, house, reopened) = rig()
        let gate = Gate()
        house.voiceBody.set(Self.voiceAnswer(followup: true))
        house.gates.set(["/api/chat/threads/7/voice": gate])
        await open(model, 7)

        XCTAssertTrue(model.talk(pcm: Self.clip))
        await settleUntil({ gate.arrived.current }, "the voice request")
        // Inside the round trip: the request has reached the server and its
        // answer has not come back.
        model.open(thread: 8)
        await settleUntil({ model.jobs.first?.id == "j7" }, "thread 8")
        gate.release()
        await everythingLands()

        XCTAssertNil(model.replyAudio, "thread 7's audio did not follow the person to thread 8")
        XCTAssertNil(model.talkNote)
        XCTAssertFalse(model.followup)
        XCTAssertEqual(model.talkPhase, .idle)
        XCTAssertEqual(reopened.current, 0, "a conversation nobody is looking at is not re-read")
        XCTAssertEqual(model.detail.rows.keys.sorted(), [20, 21, 31])
    }

    /// The same guard on the card stream, which writes four more fields.
    func testACardAnswerLandingAfterASwitchWritesNothing() async {
        let (model, house, reopened) = rig()
        let gate = Gate()
        house.gates.set(["/api/chat/threads/7/lookups/12": gate])
        await open(model, 7)
        XCTAssertTrue(model.decideLookup(message: 12, decision: "chat"))
        await settleUntil({ gate.arrived.current }, "the lookup request")
        model.open(thread: 8)
        gate.release()
        await everythingLands()

        XCTAssertEqual(model.cardReply, "", "no streamed words in another thread")
        XCTAssertNil(model.cardError)
        XCTAssertFalse(model.answering)
        XCTAssertFalse(model.internetAllowed, "and no chip flipped on in thread 8")
        XCTAssertEqual(reopened.current, 0)
    }

    func testMovingHouseForgetsEverything() async {
        let (model, _, _) = rig()
        await open(model, 7)
        await settleUntil({ model.internetChip != nil }, "the chip")
        model.serverChanged()
        XCTAssertNil(model.thread)
        XCTAssertTrue(model.detail.rows.isEmpty)
        XCTAssertNil(model.internetChip)
        XCTAssertEqual(model.pictures.count, 0)
        await everythingLands()
        XCTAssertTrue(model.detail.rows.isEmpty, "and nothing in flight filled it back in")
    }

    // MARK: - report a message

    func testAReportNamesTheQuestionAboveTheReplyAndNothingElse() async throws {
        let (model, _, _) = rig()
        await open(model, 7)
        let report = try XCTUnwrap(model.report(for: 11, appVersion: "1.0.0"))
        XCTAssertEqual(report.question, "what does the lease say about pets?")
        XCTAssertTrue(report.spoken, "that question was spoken")
        XCTAssertEqual(report.answer, "Cats are allowed with written consent.")
        XCTAssertEqual(report.model, "llama3.2:latest")
        // The reply's own stamp, not the clock: a report opened a day later
        // still says when the answer was written.
        XCTAssertEqual(report.answeredAt, ChatMoreModel.date("2026-11-02T18:00:00+00:00"))
        XCTAssertNil(model.report(for: 10, appVersion: "1.0.0"), "a question is not reportable")
        XCTAssertNil(model.report(for: 99, appVersion: "1.0.0"), "a row that is not there")
    }
}
