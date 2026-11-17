import Foundation
import XCTest
@testable import DomovoiKit

/// A video in a home-server chat, against a house that is not a house: the
/// request the upload makes (path, query, headers, the file as the body),
/// the checks that refuse a clip before anything is sent, the refusal
/// mapping, the file let go exactly once, the send body, the polling that
/// starts and stops with a job's state, stop, and what was kept.
@MainActor
final class ChatVideoModelTests: XCTestCase {

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

    /// The poll's wait, as a gate a test opens one tick at a time.
    private final class Ticks: Sendable {
        let asked = Shared<[Int64]>([])
        private let allowed = Shared<Int>(0)

        func tick() { allowed.mutate { $0 += 1 } }

        var sleeper: @Sendable (Int64) async throws -> Void {
            let asked = self.asked
            let allowed = self.allowed
            return { ms in
                let mine = asked.mutate { list -> Int in
                    list.append(ms)
                    return list.count
                }
                for _ in 0..<settleYieldLimit {
                    if allowed.current >= mine { return }
                    try Task.checkCancellation()
                    await Task.yield()
                }
                throw CancellationError()
            }
        }
    }

    /// The house's chat-video routes.
    private final class House: Sendable {
        /// `GET /threads/{id}/messages`'s `videos`, by thread id.
        let videos = Shared<[Int64: String]>([:])
        /// What `GET /video-jobs/{id}` answers, in turn (the last one repeats).
        let jobViews = Shared<[String]>([])
        let uploadStatus = Shared<Int>(200)
        let uploadBody = Shared<String>(
            #"{"token":"0123456789abcdef0123456789abcdef","name":"party one.mov","duration_s":151.3,"size":412345678,"#
                + #""width":1920,"height":1080,"has_audio":true,"estimate_s":372.4,"estimate_text":"about 6 minutes"}"#
        )
        let uploadHeaders = Shared<[String: String]>([:])
        let cancelStatus = Shared<Int>(200)
        let cancelBody = Shared<String>("{}")
        let resultsStatus = Shared<Int>(200)
        let results = Shared<String>("""
        {"job":{"id":"aa","state":"done","line":"Watched"},"summary":"Someone sings happy birthday.",
         "transcript":[{"start_s":0.85,"end_s":9.1,"text":"happy birthday to you"}],
         "frames":[{"at_s":1.0,"might_show":"a cake with candles on a table"}]}
        """)
        let stream = Shared<String>("""
        event: delta
        data: {"text":"I'm watching party one.mov (2:31) now."}

        event: done
        data: {"id":21,"role":"assistant","content":"I'm watching party one.mov (2:31) now.","stats":{"model_role":"video"}}

        """)
        /// A path held open until `release` is set (bounded under the
        /// session's 1 s timeout, as `parkRequest` is).
        let parkPath = Shared<String?>(nil)
        let parkArrived = Shared<Bool>(false)
        let parkRelease = Shared<Bool>(false)
        /// The park hit its bound instead of being released: whatever the
        /// case asserted afterwards was asserted over a timed-out request.
        let parkRanOut = Shared<Bool>(false)
        /// The parked request has been let go and is being answered.
        let parkLeft = Shared<Bool>(false)

        /// "METHOD path" of every request but the identity probe.
        let seen = Shared<[String]>([])

        func count(_ line: String) -> Int { seen.current.filter { $0 == line }.count }

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url,
                      let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let method = request.httpMethod ?? "GET"
                let path = parts.path
                if path == "/api/health" {
                    let challenge = parts.queryItems?.first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                }
                self.seen.mutate { $0.append("\(method) \(path)") }
                if let parked = self.parkPath.current, parked == path {
                    self.parkArrived.set(true)
                    for step in 0..<400 {
                        if self.parkRelease.current { break }
                        if step == 399 { self.parkRanOut.set(true) }
                        Thread.sleep(forTimeInterval: 0.002)
                    }
                    self.parkLeft.set(true)
                }
                switch (method, path) {
                case ("POST", "/api/chat/videos"):
                    return .json(self.uploadBody.current, status: self.uploadStatus.current,
                                 headers: self.uploadHeaders.current)
                case ("POST", "/api/chat/threads"):
                    return .json(#"{"id":5,"title":null}"#)
                default:
                    break
                }
                if path.hasPrefix("/api/chat/threads/"), path.hasSuffix("/messages") {
                    if method == "GET" {
                        let id = Int64(path.dropFirst("/api/chat/threads/".count).dropLast("/messages".count)) ?? 0
                        let videos = self.videos.current[id] ?? "[]"
                        return .json(#"{"messages":[],"jobs":[],"videos":\#(videos)}"#)
                    }
                    return .json(self.stream.current, headers: ["Content-Type": "text/event-stream"])
                }
                if path.hasPrefix("/api/chat/video-jobs/") {
                    if path.hasSuffix("/cancel") {
                        return .json(self.cancelBody.current, status: self.cancelStatus.current)
                    }
                    if path.hasSuffix("/results") {
                        return .json(self.results.current, status: self.resultsStatus.current)
                    }
                    let next = self.jobViews.mutate { views -> String in
                        guard let first = views.first else { return "{}" }
                        if views.count > 1 { views.removeFirst() }
                        return first
                    }
                    return .json(next)
                }
                return .json("{}", status: 404)
            }
        }
    }

    private func container() -> AppContainer {
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        return AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
    }

    private func rig(
        house: House = House(),
        measure: ChatVideoMeasure = ChatVideoMeasure(seconds: 151.3, bytes: 412_345_678),
        ticks: Ticks = Ticks(),
        ended: Shared<Int> = Shared<Int>(0)
    ) -> (ChatVideoModel, House) {
        house.install()
        let model = ChatVideoModel(
            in: container(),
            measure: { _ in measure },
            wait: ticks.sleeper,
            onEnded: { ended.mutate { $0 += 1 } }
        )
        return (model, house)
    }

    private let offer = ChatVideoOffer(available: true)

    /// A file on disk, in a folder deleted when the case ends, and a counter
    /// of how many times it was let go.
    private func clip(
        _ bytes: Data = Data("a clip, longer than nothing".utf8),
        named name: String = "party one.mov",
        mime: String? = "video/quicktime"
    ) throws -> (ChatVideoFile, Shared<Int>) {
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("chat-video-model-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: folder) }
        let url = folder.appendingPathComponent(name)
        try bytes.write(to: url)
        let released = Shared<Int>(0)
        let file = ChatVideoFile(url: url, name: name, mime: mime) { released.mutate { $0 += 1 } }
        return (file, released)
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

    /// For asserting a negative: everything that could run has had its turn.
    private func everythingLands() async {
        for _ in 0..<settleYieldLimit { await Task.yield() }
    }

    private func uploads() -> [URLRequest] {
        StubURLProtocol.recorded.filter { $0.url?.path == "/api/chat/videos" }
    }

    // MARK: - the upload

    /// **The request.** The path and its encoded name, the video's own type,
    /// the header every write carries, the household token, and the file's
    /// bytes as the body — not multipart, not a copy in memory.
    func testTheUploadSendsTheFileAsTheBodyWithItsNameAndType() async throws {
        let (model, _) = rig()
        let bytes = Data((0..<70_000).map { UInt8($0 % 251) })
        let (file, released) = try clip(bytes)

        XCTAssertTrue(model.attach(file, offer: offer))
        XCTAssertEqual(.sending(name: "party one.mov", percent: nil), model.staged, "on the composer at once")
        XCTAssertTrue(model.uploading)
        await settleUntil({ model.readyToSend }, "the upload's answer")

        let sent = try XCTUnwrap(uploads().first)
        XCTAssertEqual(1, uploads().count)
        XCTAssertEqual("POST", sent.httpMethod)
        XCTAssertEqual(
            "name=party%20one.mov",
            sent.url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }?.percentEncodedQuery
        )
        XCTAssertEqual("video/quicktime", sent.value(forHTTPHeaderField: "Content-Type"))
        XCTAssertEqual("DomovoiApp", sent.value(forHTTPHeaderField: "X-Requested-With"),
                       "the route requires it, as every write does")
        XCTAssertEqual("household-abc", sent.value(forHTTPHeaderField: deviceTokenHeader))
        XCTAssertEqual(bytes, sent.httpBody, "the body is the FILE")

        guard case .ready(let upload) = model.staged else { return XCTFail("not ready: \(String(describing: model.staged))") }
        XCTAssertEqual("0123456789abcdef0123456789abcdef", upload.token)
        XCTAssertEqual("2:31 · 393 MB · watching takes about 6 minutes", ChatVideoStrings.stagedSummary(upload))
        await settleUntil({ released.current == 1 }, "the file let go")
        XCTAssertNil(model.toast)
    }

    /// A type the picker did not say is the name's own.
    func testWithNoTypeTheNameDecidesItAndANamelessClipTakesItsTypesEnding() async throws {
        let (model, _) = rig()
        let (file, _) = try clip(named: "party.mp4", mime: nil)
        model.attach(file, offer: offer)
        await settleUntil({ model.readyToSend }, "the upload")
        XCTAssertEqual("video/mp4", uploads().first?.value(forHTTPHeaderField: "Content-Type"))

        model.removeStaged()
        let (bare, _) = try clip(named: "clip", mime: "video/quicktime")
        model.attach(bare, offer: offer)
        await settleUntil({ model.readyToSend }, "the second upload")
        XCTAssertEqual(
            "name=clip.mov",
            uploads().last?.url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }?.percentEncodedQuery,
            "a clip a picker named without an ending goes up with its type's"
        )
    }

    // MARK: - checked on the phone first

    func testATooLongClipIsRefusedBeforeAByteIsSent() async throws {
        let (model, _) = rig(measure: ChatVideoMeasure(seconds: 222, bytes: 1_000))
        let (file, released) = try clip()
        XCTAssertTrue(model.attach(file, offer: offer))
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual("Videos up to 3 minutes can be attached. This one is 3:42 long.", model.toast)
        XCTAssertNil(model.staged, "nothing left on the composer")
        XCTAssertEqual(0, uploads().count, "nothing was sent")
        XCTAssertEqual(1, released.current, "the file is let go")
    }

    func testATooLargeClipIsRefusedBeforeAByteIsSent() async throws {
        let (model, _) = rig(measure: ChatVideoMeasure(seconds: 60, bytes: 2048 * 1024 * 1024 + 1))
        let (file, released) = try clip()
        model.attach(file, offer: offer)
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual(
            "Videos up to 2 GB can be attached. Send a shorter clip, or record it at a lower quality.",
            model.toast
        )
        XCTAssertEqual(0, uploads().count)
        XCTAssertEqual(1, released.current)
    }

    func testAFormatChatDoesNotTakeIsRefusedAtOnce() throws {
        let (model, _) = rig()
        let (file, released) = try clip(named: "film.avi", mime: "video/x-msvideo")
        XCTAssertFalse(model.attach(file, offer: offer))
        XCTAssertEqual(ChatVideoStrings.unsupported, model.toast)
        XCTAssertNil(model.staged)
        XCTAssertEqual(1, released.current)
    }

    func testOneVideoAMessage() async throws {
        let (model, _) = rig()
        let (first, _) = try clip()
        model.attach(first, offer: offer)
        await settleUntil({ model.readyToSend }, "the first clip")
        let (second, released) = try clip(named: "second.mp4")
        XCTAssertFalse(model.attach(second, offer: offer))
        XCTAssertEqual("one video a message", model.toast)
        XCTAssertEqual(1, released.current, "the second file is let go at once")
        XCTAssertEqual(1, uploads().count)
    }

    /// The view never draws a door the house cannot serve; the model refuses
    /// one all the same, silently.
    func testNothingIsTakenWhenTheHouseCannotWatchOne() throws {
        let (model, _) = rig()
        for refused in [nil, ChatVideoOffer(available: false, reason: "ai-off")] {
            let (file, released) = try clip()
            XCTAssertFalse(model.attach(file, offer: refused))
            XCTAssertEqual(1, released.current)
        }
        XCTAssertNil(model.staged)
        XCTAssertNil(model.toast)
    }

    // MARK: - the refusals

    func testEachUploadRefusalShowsTheServersSentence() async throws {
        let cases: [(Int, String, [String: String], String)] = [
            (415, #"{"detail":"Chat takes MP4, MOV and WebM videos.","code":"video_unsupported"}"#, [:],
             "Chat takes MP4, MOV and WebM videos."),
            (422, #"{"detail":"Domovoi couldn't read that video.","code":"video_unreadable"}"#, [:],
             "Domovoi couldn't read that video."),
            (503, #"{"detail":"This home server can't read videos.","code":"video_no_tools"}"#, [:],
             "This home server can't read videos."),
            (507, #"{"detail":"The home server is short of disk space.","code":"video_no_room"}"#, [:],
             "The home server is short of disk space."),
            (413, "Request Entity Too Large", [:], ChatVideoStrings.tooLarge),
            (409, #"{"detail":"AI features are turned off for this home"}"#, ["X-Domovoi-Refusal": "ai-off"],
             aiChatOffText),
        ]
        for (status, body, headers, said) in cases {
            let house = House()
            house.uploadStatus.set(status)
            house.uploadBody.set(body)
            house.uploadHeaders.set(headers)
            let (model, _) = rig(house: house)
            let (file, released) = try clip()
            model.attach(file, offer: offer)
            await settleUntil({ model.toast != nil }, "the \(status) refusal")
            XCTAssertEqual(said, model.toast, "\(status)")
            XCTAssertNil(model.staged, "\(status): nothing left staged")
            await settleUntil({ released.current == 1 }, "\(status): the file let go")
        }
    }

    /// **Removed while on its way**: the request is stopped, the composer is
    /// clear, the file is let go, and nothing is said — the person did it.
    func testRemovingAnUploadOnItsWayStopsItAndSaysNothing() async throws {
        let house = House()
        house.parkPath.set("/api/chat/videos")
        let (model, _) = rig(house: house)
        let (file, released) = try clip()
        model.attach(file, offer: offer)
        await settleUntil({ house.parkArrived.current }, "the upload reaching the house")

        model.removeStaged()
        XCTAssertNil(model.staged, "off the composer at once")
        house.parkRelease.set(true)
        await settleUntil({ released.current == 1 }, "the file let go")
        await everythingLands()
        XCTAssertNil(model.staged, "a late answer does not put it back")
        XCTAssertNil(model.toast, "nothing is said about a stop the person asked for")
        XCTAssertEqual(1, released.current, "once")
    }

    // MARK: - the send

    func testTheTokenGoesWithTheSendAndPutsTheClipUnderTheNewRow() async throws {
        let (model, _) = rig()
        let (file, _) = try clip()
        model.attach(file, offer: offer)
        await settleUntil({ model.readyToSend }, "the clip")

        let token = model.takeForSend()
        XCTAssertEqual("0123456789abcdef0123456789abcdef", token)
        XCTAssertNil(model.staged, "taken off the composer")
        XCTAssertEqual(token, model.justSent?.token, "drawn under the person's row until the job arrives")

        model.sendRefused(token)
        XCTAssertTrue(model.readyToSend, "a send that did not go puts the clip back")
        XCTAssertNil(model.justSent)

        _ = model.takeForSend()
        XCTAssertNil(model.takeForSend(), "a text-only send takes nothing")
        XCTAssertNil(model.justSent, "and the last clip is not drawn under a message it was not sent with")
    }

    /// `ChatModel` sends `videos: [{token}]`, takes a blank message with one,
    /// and reads the thread again afterwards so the chip finds its row.
    func testTheSendBodyCarriesTheVideoAndABlankMessageGoes() async throws {
        let house = House()
        house.install()
        let chat = ChatModel(in: container(), deviceID: "ios-1", pins: ChatPinBook(place: SavedStateBox()))
        chat.open(thread: 5, title: "a party")
        await settleUntil({ house.count("GET /api/chat/threads/5/messages") == 1 }, "the thread")

        XCTAssertFalse(chat.send("   "), "a blank message with no video sends nothing")
        XCTAssertTrue(chat.send("", videoToken: "0123456789abcdef0123456789abcdef"))
        await settleUntil({ !chat.sending }, "the reply")
        await settleUntil({ house.count("GET /api/chat/threads/5/messages") == 2 }, "the re-read after a video send")

        let post = try XCTUnwrap(StubURLProtocol.recorded.first {
            $0.httpMethod == "POST" && $0.url?.path == "/api/chat/threads/5/messages"
        })
        let body = try XCTUnwrap(try JSONSerialization.jsonObject(with: XCTUnwrap(post.httpBody)) as? [String: Any])
        XCTAssertEqual("", body["content"] as? String)
        XCTAssertEqual([["token": "0123456789abcdef0123456789abcdef"]], body["videos"] as? [[String: String]])
        XCTAssertEqual("ios-1", body["device_id"] as? String)
    }

    // MARK: - following a job

    private func view(
        _ state: String,
        line: String,
        message: Int64 = 10,
        cancel: Bool = false,
        id: String = "aa",
        thread: Int64 = 5
    ) -> String {
        #"{"id":"\#(id)","thread_id":\#(thread),"message_id":\#(message),"name":"party.mp4","duration_s":151.3,"#
            + #""state":"\#(state)","line":"\#(line)","cancel_requested":\#(cancel),"#
            + #""progress":{"pieces_done":4,"pieces":18,"frames_done":0,"frames":0}}"#
    }

    /// **Polled while queued or running, every 3 s, and not after.** When it
    /// ends, the thread is read again once (the core wrote its summary into
    /// it), and nothing keeps reading.
    func testAJobIsPolledWhileLiveAndThePollStopsWhenItEnds() async throws {
        let house = House()
        house.videos.set([5: "[\(view("queued", line: "Waiting to start…"))]"])
        house.jobViews.set([
            view("running", line: "Watching your video… about 4 minutes left"),
            view("done", line: "Watched"),
        ])
        let ticks = Ticks()
        let ended = Shared<Int>(0)
        let (model, _) = rig(house: house, ticks: ticks, ended: ended)

        model.open(thread: 5)
        await settleUntil({ model.jobs.count == 1 }, "the thread's videos")
        XCTAssertEqual(["aa"], model.jobs(under: 10).map(\.id), "the chip goes under the person's row")
        await settleUntil({ ticks.asked.current.count == 1 }, "the first wait")
        XCTAssertEqual([3_000], ticks.asked.current, "every 3 s")
        XCTAssertEqual(0, house.count("GET /api/chat/video-jobs/aa"), "nothing is read before the wait")

        ticks.tick()
        await settleUntil({ model.jobs.first?.state == "running" }, "the running view")
        XCTAssertEqual("Watching your video… about 4 minutes left", model.jobs.first?.line)
        XCTAssertEqual(0, ended.current)
        await settleUntil({ ticks.asked.current.count == 2 }, "the second wait")

        ticks.tick()
        await settleUntil({ model.jobs.first?.state == "done" }, "the done view")
        await settleUntil({ ended.current == 1 }, "the thread read again")
        ticks.tick()
        await everythingLands()
        XCTAssertEqual(2, ticks.asked.current.count, "no wait after the job ended")
        XCTAssertEqual(2, house.count("GET /api/chat/video-jobs/aa"), "and no read")
        XCTAssertEqual(1, ended.current, "once")
    }

    func testAThreadWithNoLiveJobIsNotPolled() async throws {
        let house = House()
        house.videos.set([5: "[\(view("done", line: "Watched")),\(view("cancelled", line: "Stopped"))]"])
        let ticks = Ticks()
        let (model, _) = rig(house: house, ticks: ticks)
        model.open(thread: 5)
        await settleUntil({ model.jobs.count == 2 }, "the thread's videos")
        await everythingLands()
        XCTAssertEqual([], ticks.asked.current)
        XCTAssertEqual(0, house.count("GET /api/chat/video-jobs/aa"))
    }

    func testLeavingTheThreadStopsThePoll() async throws {
        let house = House()
        house.videos.set([5: "[\(view("running", line: "Watching your video…"))]"])
        house.jobViews.set([view("running", line: "Watching your video…")])
        let ticks = Ticks()
        let (model, _) = rig(house: house, ticks: ticks)
        model.open(thread: 5)
        await settleUntil({ ticks.asked.current.count == 1 }, "the first wait")
        model.close()
        ticks.tick()
        await everythingLands()
        XCTAssertEqual(0, house.count("GET /api/chat/video-jobs/aa"), "nothing read for a thread that is gone")
        XCTAssertEqual([], model.jobs.map(\.id))
    }

    /// [[read-after-await-wrong-key]]: thread 5's videos, answered after the
    /// person left thread 5, must not land.
    ///
    /// **Released before anything else is waited for.** A parked
    /// `URLProtocol` holds the thread it is loading on (``ParkedRequest`` and
    /// `HealthPanelModelTests` say so), so no other request reaches the stub
    /// until the park is let go. CI run 44011423168 failed this case when it
    /// waited for thread 6's read to arrive *during* the park: that read was
    /// queued behind thread 5's in the stub, not lost in the model.
    ///
    /// Two halves, because the stub answers in order: thread 5's answer always
    /// lands before anything asked after it.
    ///
    /// * **Left for nothing** (``ChatVideoModel/close()``): the only answer is
    ///   thread 5's, so without the epoch guard its job would be on screen.
    ///   This is the half that fails if the guard goes.
    /// * **Left for thread 6**: thread 6's own job ends up on screen and stays.
    func testAnOldThreadsVideosDoNotLandInTheNewOne() async throws {
        let house = House()
        house.videos.set([
            5: "[\(view("done", line: "Watched"))]",
            6: "[\(view("done", line: "Watched", message: 60, id: "bb", thread: 6))]",
        ])
        house.parkPath.set("/api/chat/threads/5/messages")
        let (model, _) = rig(house: house)

        model.open(thread: 5)
        await settleUntil({ house.parkArrived.current }, "thread 5's read")
        model.close()
        house.parkRelease.set(true)
        await settleUntil({ house.parkLeft.current }, "thread 5's answer")
        await everythingLands()
        XCTAssertFalse(house.parkRanOut.current, "the park timed out, so this asserts nothing")
        XCTAssertEqual([], model.jobs.map(\.id), "thread 5's answer landed after the person left it")
        XCTAssertNil(model.thread)

        house.parkArrived.set(false)
        house.parkRelease.set(false)
        house.parkLeft.set(false)
        model.open(thread: 5)
        await settleUntil({ house.parkArrived.current }, "thread 5's second read")
        model.open(thread: 6)
        house.parkRelease.set(true)
        await settleUntil({ model.jobs.map(\.id) == ["bb"] }, "thread 6's read")
        await everythingLands()
        XCTAssertFalse(house.parkRanOut.current, "the park timed out, so this asserts nothing")
        XCTAssertEqual(["bb"], model.jobs.map(\.id), "thread 5's job under thread 6")
        XCTAssertEqual(6, model.thread)
        XCTAssertEqual(1, house.count("GET /api/chat/threads/6/messages"))
    }

    // MARK: - stop

    func testStopAsksTheHouseAndPutsItsAnswerInPlace() async throws {
        let house = House()
        house.videos.set([5: "[\(view("running", line: "Watching your video…"))]"])
        house.cancelBody.set(view("running", line: "Stopping…", cancel: true))
        let ended = Shared<Int>(0)
        let (model, _) = rig(house: house, ended: ended)
        model.open(thread: 5)
        await settleUntil({ model.jobs.count == 1 }, "the job")
        let job = try XCTUnwrap(model.jobs.first)
        XCTAssertTrue(ChatVideoRules.stopOffered(job))

        model.stop(job)
        XCTAssertTrue(model.stopping.contains("aa"), "stopping… while it is asked")
        model.stop(job)
        await settleUntil({ model.jobs.first?.cancelRequested == true }, "the cancel's answer")
        XCTAssertEqual(1, house.count("POST /api/chat/video-jobs/aa/cancel"), "one stop, however often it is tapped")
        XCTAssertEqual("Stopping…", model.jobs.first?.line)
        XCTAssertFalse(model.stopping.contains("aa"))
        XCTAssertFalse(ChatVideoRules.stopOffered(try XCTUnwrap(model.jobs.first)), "no second stop")
        XCTAssertEqual(0, ended.current, "still running")

        // A queued one ends at once, and the thread is read again.
        house.videos.set([5: "[\(view("queued", line: "Waiting to start…", message: 11))]"])
        house.cancelBody.set(view("cancelled", line: "Stopped", message: 11))
        model.close()
        model.open(thread: 5)
        await settleUntil({ model.jobs.first?.state == "queued" }, "the queued job")
        model.stop(try XCTUnwrap(model.jobs.first))
        await settleUntil({ model.jobs.first?.state == "cancelled" }, "stopped")
        XCTAssertEqual("Stopped", model.jobs.first?.line)
        XCTAssertEqual(1, ended.current)
    }

    func testAStopThatFailedSaysWhy() async throws {
        let house = House()
        house.videos.set([5: "[\(view("running", line: "Watching your video…"))]"])
        house.cancelStatus.set(404)
        house.cancelBody.set(#"{"detail":"video job not found"}"#)
        let (model, _) = rig(house: house)
        model.open(thread: 5)
        await settleUntil({ model.jobs.count == 1 }, "the job")
        model.stop(try XCTUnwrap(model.jobs.first))
        await settleUntil({ model.toast != nil }, "the refusal")
        XCTAssertEqual("video job not found", model.toast)
        XCTAssertTrue(model.stopping.isEmpty)
    }

    // MARK: - what was kept

    func testWhatWasKeptIsReadOnceWhenFirstOpened() async throws {
        let house = House()
        house.videos.set([5: "[\(view("done", line: "Watched"))]"])
        let (model, _) = rig(house: house)
        model.open(thread: 5)
        await settleUntil({ model.jobs.count == 1 }, "the job")
        let job = try XCTUnwrap(model.jobs.first)
        XCTAssertNil(model.kept["aa"], "nothing is read until it is opened")

        model.toggleKept(job)
        XCTAssertTrue(model.keptOpen.contains("aa"))
        XCTAssertEqual(.loading, model.kept["aa"])
        await settleUntil({ model.kept["aa"] != .loading }, "what was kept")
        guard case .loaded(let results)? = model.kept["aa"] else { return XCTFail("not loaded") }
        XCTAssertEqual("happy birthday to you", results.transcript.first?.text)
        XCTAssertEqual("a cake with candles on a table", results.frames.first?.mightShow)
        XCTAssertEqual("Someone sings happy birthday.", results.summary)

        model.toggleKept(job)
        XCTAssertFalse(model.keptOpen.contains("aa"))
        model.toggleKept(job)
        await everythingLands()
        XCTAssertEqual(1, house.count("GET /api/chat/video-jobs/aa/results"), "read once")
    }

    func testWhatWasKeptThatCouldNotBeReadIsReadAgainNextTime() async throws {
        let house = House()
        house.videos.set([5: "[\(view("done", line: "Watched"))]"])
        house.resultsStatus.set(500)
        let (model, _) = rig(house: house)
        model.open(thread: 5)
        await settleUntil({ model.jobs.count == 1 }, "the job")
        let job = try XCTUnwrap(model.jobs.first)
        model.toggleKept(job)
        await settleUntil({ model.kept["aa"] == .failed }, "the failed read")
        model.toggleKept(job)
        house.resultsStatus.set(200)
        model.toggleKept(job)
        await settleUntil({ model.kept["aa"] != .loading && model.kept["aa"] != .failed }, "the second read")
        XCTAssertEqual(2, house.count("GET /api/chat/video-jobs/aa/results"))
    }

    func testOnlyAWatchedJobOpensWhatWasKept() async throws {
        let (model, house) = rig()
        model.toggleKept(ChatVideoJob(id: "aa", state: "failed"))
        model.toggleKept(ChatVideoJob(id: "aa", state: "running"))
        await everythingLands()
        XCTAssertTrue(model.keptOpen.isEmpty)
        XCTAssertEqual(0, house.count("GET /api/chat/video-jobs/aa/results"))
    }
}
