import Foundation
import XCTest
@testable import DomovoiKit

/// The pure half of a video in a home-server chat: the gate, the phone's own
/// length and size checks with the server's sentences, what a refusal says,
/// the shapes as the server sends them, the send body and the paths.
///
/// The sentences are asserted **as literals**, copied from
/// `domovoi/chat_video/limits.py`, so a change to either side's words is a
/// red here rather than two phones and a server saying different things.
final class ChatVideoRulesTests: XCTestCase {

    // MARK: - the gate

    private func models(_ json: String) throws -> ChatModelsInfo {
        try JSONDecoder().decode(ChatModelsInfo.self, from: Data(json.utf8))
    }

    func testAVideoIsOfferedOnlyWhenTheHouseSaysAvailable() throws {
        let on = try models("""
        {"installed":["qwen3:8b"],"vision_installed":true,
         "video":{"available":true,"reason":null,"max_seconds":180,"max_mb":2048,
                  "accept":"video/mp4,video/quicktime,video/webm,.mp4,.m4v,.mov,.webm",
                  "extensions":[".mp4",".m4v",".mov",".webm"]}}
        """)
        XCTAssertTrue(ChatVideoRules.offered(on))
        XCTAssertEqual(180, on.video?.maxSeconds)
        XCTAssertEqual(2048, on.video?.maxMb)
        XCTAssertEqual([".mp4", ".m4v", ".mov", ".webm"], on.video?.extensions)
        XCTAssertEqual(true, on.visionInstalled, "the video key costs the vision fields nothing")

        let aiOff = try models(#"{"video":{"available":false,"reason":"ai-off"}}"#)
        XCTAssertFalse(ChatVideoRules.offered(aiOff), "AI features off: no video control at all")
        XCTAssertEqual("ai-off", aiOff.video?.reason)

        let noTools = try models(#"{"video":{"available":false,"reason":"no-ffmpeg"}}"#)
        XCTAssertFalse(ChatVideoRules.offered(noTools))

        let older = try models(#"{"installed":["qwen3:8b"],"vision_installed":false}"#)
        XCTAssertNil(older.video, "a server from before 1.4.0 has no video key")
        XCTAssertFalse(ChatVideoRules.offered(older), "absent is false, never 'probably fine'")

        XCTAssertFalse(ChatVideoRules.offered(nil), "a models read that never answered offers nothing")
    }

    /// A `video` object this app cannot read is a house that did not say
    /// yes — and it must not cost the rest of the models answer.
    func testAnUnreadableVideoKeyIsNoAndKeepsTheRest() throws {
        let odd = try models(#"{"vision_model":"llava:7b","vision_installed":false,"video":"yes"}"#)
        XCTAssertNil(odd.video)
        XCTAssertFalse(ChatVideoRules.offered(odd))
        XCTAssertEqual("llava:7b", odd.visionModel)
    }

    /// A limit of zero or less is a server that said nothing useful: the
    /// house's own constants stand in rather than refusing every clip.
    func testMissingOrSillyLimitsFallBackToTheHousesOwn() throws {
        let bare = try models(#"{"video":{"available":true,"max_seconds":0}}"#)
        XCTAssertEqual(ChatVideoRules.maxSeconds, bare.video?.maxSeconds)
        XCTAssertEqual(ChatVideoRules.maxMb, bare.video?.maxMb)
        XCTAssertEqual(ChatVideoRules.extensions, bare.video?.extensions)
    }

    // MARK: - checked on the phone first

    func testTheLengthCheckHasTheServersHalfSecondOfSlack() {
        let offer = ChatVideoOffer(available: true)
        XCTAssertNil(ChatVideoRules.refusal(uploadName: "party.mp4", seconds: 180.0, bytes: 1_000, offer: offer))
        XCTAssertNil(
            ChatVideoRules.refusal(uploadName: "party.mp4", seconds: 180.5, bytes: 1_000, offer: offer),
            "180 s + 0.5 s of container rounding is still taken, as the server takes it"
        )
        XCTAssertEqual(
            "Videos up to 3 minutes can be attached. This one is 3:01 long.",
            ChatVideoRules.refusal(uploadName: "party.mp4", seconds: 180.6, bytes: 1_000, offer: offer)
        )
        XCTAssertEqual(
            "Videos up to 3 minutes can be attached. This one is 3:42 long.",
            ChatVideoRules.refusal(uploadName: "party.mp4", seconds: 222.0, bytes: 1_000, offer: offer),
            "the handoff's own example, word for word"
        )
    }

    func testTheSizeCheckIsTwoGibibytesAndSaysTheServersSentence() {
        let offer = ChatVideoOffer(available: true)
        let limit: Int64 = 2048 * 1024 * 1024
        XCTAssertNil(ChatVideoRules.refusal(uploadName: "party.mp4", seconds: 30, bytes: limit, offer: offer), "2 GiB exactly is taken")
        XCTAssertEqual(
            "Videos up to 2 GB can be attached. Send a shorter clip, or record it at a lower quality.",
            ChatVideoRules.refusal(uploadName: "party.mp4", seconds: 30, bytes: limit + 1, offer: offer)
        )
        XCTAssertEqual(
            ChatVideoStrings.tooLarge,
            ChatVideoRules.refusal(uploadName: "party.mp4", seconds: 600, bytes: limit + 1, offer: offer),
            "a clip both too long and too big is refused for its size, the server's order"
        )
    }

    /// A length or size the phone could not read is the server's to decide —
    /// the dashboard sends such a clip and lets ffprobe answer.
    func testWhatThePhoneCouldNotReadIsLeftToTheServer() {
        let offer = ChatVideoOffer(available: true)
        XCTAssertNil(ChatVideoRules.refusal(uploadName: "party.mp4", seconds: nil, bytes: nil, offer: offer))
        XCTAssertNil(ChatVideoRules.refusal(uploadName: "party.mp4", seconds: .nan, bytes: 10, offer: offer))
        XCTAssertNil(ChatVideoRules.refusal(uploadName: "party.mp4", seconds: .infinity, bytes: 10, offer: offer))
    }

    /// The limits the house sent, not this app's constants.
    func testTheHousesOwnLimitsAreTheOnesChecked() {
        let tighter = ChatVideoOffer(available: true, maxSeconds: 60, maxMb: 10)
        XCTAssertNotNil(ChatVideoRules.refusal(uploadName: "party.mp4", seconds: 61, bytes: 1, offer: tighter))
        XCTAssertEqual(
            ChatVideoStrings.tooLarge,
            ChatVideoRules.refusal(uploadName: "party.mp4", seconds: 1, bytes: 10 * 1024 * 1024 + 1, offer: tighter)
        )
        XCTAssertNil(ChatVideoRules.refusal(uploadName: "party.mp4", seconds: 60.4, bytes: 10 * 1024 * 1024, offer: tighter))
    }

    /// The format, checked on the phone (Android's `videoUploadName`): a name
    /// chat takes stays; a name with no such ending takes its type's; neither
    /// is refused with the server's sentence before anything is read.
    func testTheFormatIsCheckedByNameThenByType() {
        XCTAssertEqual("party.mp4", ChatVideoRules.uploadName("party.mp4", mime: nil))
        XCTAssertEqual("IMG_0042.MOV", ChatVideoRules.uploadName("IMG_0042.MOV", mime: "video/quicktime"))
        XCTAssertEqual("clip.mov", ChatVideoRules.uploadName("clip", mime: "video/quicktime"))
        XCTAssertEqual("clip.m4v", ChatVideoRules.uploadName("clip", mime: "video/x-m4v; codecs=avc1"))
        XCTAssertEqual("video.mp4", ChatVideoRules.uploadName("  ", mime: "video/mp4"), "a nameless clip")
        XCTAssertEqual("film.mkv.webm", ChatVideoRules.uploadName("film.mkv", mime: "video/webm"))
        XCTAssertNil(ChatVideoRules.uploadName("film.mkv", mime: "video/x-matroska"))
        XCTAssertNil(ChatVideoRules.uploadName("film.avi", mime: nil))
        XCTAssertNil(ChatVideoRules.uploadName("clip.webm", mime: nil, extensions: [".mp4"]),
                     "the house's own list, not this app's")

        XCTAssertEqual(
            "Chat takes MP4, MOV and WebM videos (H.264 or HEVC, the way phones record them). "
                + "Share the video from your phone as MP4 and try again.",
            ChatVideoRules.refusal(uploadName: nil, seconds: 10, bytes: 10, offer: ChatVideoOffer(available: true))
        )
    }

    // MARK: - the clock and the size, as the server and the dashboard write them

    func testTheClockIsTheServers() {
        XCTAssertEqual("0:00", ChatVideoRules.clock(0))
        XCTAssertEqual("0:00", ChatVideoRules.clock(nil))
        XCTAssertEqual("0:00", ChatVideoRules.clock(-4))
        XCTAssertEqual("2:05", ChatVideoRules.clock(125))
        XCTAssertEqual("2:31", ChatVideoRules.clock(151.3))
        XCTAssertEqual("3:00", ChatVideoRules.clock(180.4))
        XCTAssertEqual("1:02:05", ChatVideoRules.clock(3725))
        // Python's round() is half-to-even: round(222.5) == 222.
        XCTAssertEqual("3:42", ChatVideoRules.clock(222.5), "half rounds to even, as the server's round() does")
        XCTAssertEqual("3:44", ChatVideoRules.clock(223.5))
    }

    func testTheSizeTextIsTheDashboards() {
        XCTAssertEqual("1 MB", ChatVideoRules.sizeText(0), "never 0 MB")
        XCTAssertEqual("1 MB", ChatVideoRules.sizeText(1_000))
        XCTAssertEqual("393 MB", ChatVideoRules.sizeText(412_345_678))
        XCTAssertEqual("1.9 GB", ChatVideoRules.sizeText(2_000_000_000))
    }

    func testTheStagedSummaryCarriesTheEstimate() {
        let upload = ChatVideoUpload(
            token: "t", name: "party.mp4", durationS: 151.3, size: 412_345_678,
            estimateS: 372.4, estimateText: "about 6 minutes"
        )
        XCTAssertEqual("2:31 · 393 MB · watching takes about 6 minutes", ChatVideoStrings.stagedSummary(upload))
        let bare = ChatVideoUpload(token: "t", name: "party.mp4", durationS: 10, size: 5_000_000)
        XCTAssertEqual("0:10 · 5 MB", ChatVideoStrings.stagedSummary(bare))
        XCTAssertEqual("sending… 42%", ChatVideoStrings.sending(percent: 42))
        XCTAssertEqual("sending… 0%", ChatVideoStrings.sending(percent: nil))
        XCTAssertEqual("a video (up to 3 minutes)", ChatVideoStrings.menuItem, "Android's item, word for word")
        XCTAssertEqual("one video a message", ChatVideoStrings.oneVideoAMessage)
        XCTAssertEqual("remove party.mp4", ChatVideoStrings.remove("party.mp4"))
        XCTAssertEqual("stop watching; the video is deleted", ChatVideoStrings.stopHint)
        XCTAssertEqual("Watched", ChatVideoStrings.fallbackLine("done"))
        XCTAssertEqual("Waiting to start…", ChatVideoStrings.fallbackLine("queued"))
    }

    func testTheContentTypeIsTheVideosOwn() {
        XCTAssertEqual("video/mp4", ChatVideoRules.contentType(forName: "party.mp4"))
        XCTAssertEqual("video/mp4", ChatVideoRules.contentType(forName: "PARTY.M4V"))
        XCTAssertEqual("video/quicktime", ChatVideoRules.contentType(forName: "IMG_0042.MOV"))
        XCTAssertEqual("video/webm", ChatVideoRules.contentType(forName: "clip.webm"))
        XCTAssertEqual("application/octet-stream", ChatVideoRules.contentType(forName: "clip.mkv"))
    }

    // MARK: - what a refusal says

    private func refused(_ status: Int, _ body: String, refusal: String? = nil) -> DomovoiError {
        let failure = ApiFailure(status: status, message: "\(status): \(body)", body: body, refusal: refusal)
        return status >= 500 ? .serverTrouble(failure) : .httpError(failure)
    }

    /// 413, 415, 422 (both), 503 and 507 all show the server's `detail`.
    func testEveryCodedRefusalShowsTheServersSentence() {
        let cases: [(Int, String, String)] = [
            (413, "video_too_large", ChatVideoStrings.tooLarge),
            (415, "video_unsupported",
             "Chat takes MP4, MOV and WebM videos (H.264 or HEVC, the way phones record them)."),
            (422, "video_too_long", "Videos up to 3 minutes can be attached. This one is 3:42 long."),
            (422, "video_unreadable",
             "Domovoi couldn't read that video. It may be damaged, or still being saved on the phone."),
            (503, "video_no_tools", "This home server can't read videos: it needs ffmpeg, which isn't installed."),
            (507, "video_no_room",
             "The home server is short of disk space for that video. Free some space and try again."),
        ]
        for (status, code, detail) in cases {
            let body = #"{"detail":"\#(detail)","code":"\#(code)"}"#
            let error = refused(status, body)
            XCTAssertEqual(detail, ChatVideoRules.refusalText(error), "\(status) \(code)")
            XCTAssertEqual(code, ChatVideoRules.refusalCode(error), "\(status) keeps its help-desk code")
        }
    }

    func testAPlainTextFourThirteenStillSaysTheSizeSentence() {
        // The transport guard's 413 for a body over 2 GiB + 64 KiB is plain
        // text, not the server's JSON.
        let error = refused(413, "Request Entity Too Large")
        XCTAssertEqual(ChatVideoStrings.tooLarge, ChatVideoRules.refusalText(error))
        XCTAssertNil(ChatVideoRules.refusalCode(error))
    }

    func testTheAiOffRefusalSaysWhatTheRestOfChatSays() {
        let header = refused(409, #"{"detail":"something"}"#, refusal: "ai-off")
        XCTAssertEqual("AI chat is turned off on your home server", ChatVideoRules.refusalText(header))
        let bodyOnly = refused(409, #"{"detail":"AI features are turned off for this home (chat)"}"#)
        XCTAssertEqual(aiChatOffText, ChatVideoRules.refusalText(bodyOnly), "a proxy that dropped the header")
    }

    /// Anything else is worded as every other failed write: Android's
    /// `failureText("attach", e)`. A `detail` on a status that is not one of
    /// the video refusals and carries no video code is **not** shown as if
    /// it were the house refusing the clip.
    func testARefusalWithNoSentenceAndATransportFailure() {
        XCTAssertEqual("attach failed: 400:", ChatVideoRules.refusalText(refused(400, "")), "the message is trimmed, as failureText trims it")
        XCTAssertEqual(
            "attach failed: 404: {\"detail\":\"not here\"}",
            ChatVideoRules.refusalText(refused(404, #"{"detail":"not here"}"#))
        )
        XCTAssertEqual(
            "Videos up to 3 minutes can be attached.",
            ChatVideoRules.refusalText(refused(400, #"{"detail":"Videos up to 3 minutes can be attached.","code":"video_too_long"}"#)),
            "a coded refusal is shown whatever its status"
        )
        XCTAssertEqual(
            "attach failed (offline?)",
            ChatVideoRules.refusalText(.unreachable("the network connection was lost"))
        )
    }

    // MARK: - a job

    private func job(_ json: String) throws -> ChatVideoJob {
        try JSONDecoder().decode(ChatVideoJob.self, from: Data(json.utf8))
    }

    /// The handoff's own job view, field for field.
    func testTheJobViewDecodesAsTheServerSendsIt() throws {
        let view = try job("""
        {"id":"0123456789abcdef0123456789abcdef","thread_id":12,"message_id":345,"name":"party.mp4",
         "question":"","duration_s":151.3,"size":412345678,"state":"running","stage":"frames",
         "line":"Watching your video… about 4 minutes left","waiting_for_voice":false,
         "estimate_s":372.4,"eta_s":241.0,
         "progress":{"pieces_done":18,"pieces":18,"frames_done":3,"frames":9},
         "summary":null,"notes":[],"error":null,"error_code":null,"cancel_requested":false,
         "created_at":"2026-11-16T10:00:00+00:00","finished_at":null}
        """)
        XCTAssertEqual("0123456789abcdef0123456789abcdef", view.id)
        XCTAssertEqual(12, view.threadID)
        XCTAssertEqual(345, view.messageID)
        XCTAssertEqual("party.mp4", view.name)
        XCTAssertEqual(151.3, view.durationS, accuracy: 0.001)
        XCTAssertEqual(412_345_678, view.size)
        XCTAssertEqual("running", view.state)
        XCTAssertEqual("frames", view.stage)
        XCTAssertEqual("Watching your video… about 4 minutes left", view.line)
        XCTAssertEqual(241.0, view.etaS)
        XCTAssertEqual(ChatVideoProgress(piecesDone: 18, pieces: 18, framesDone: 3, frames: 9), view.progress)
        XCTAssertTrue(view.live)
        XCTAssertNil(view.summary)
        XCTAssertFalse(view.cancelRequested)
    }

    func testOnlyQueuedAndRunningAreLive() throws {
        for state in ["queued", "running"] {
            XCTAssertTrue(ChatVideoRules.isLive(state), state)
        }
        for state in ["done", "failed", "cancelled", ""] {
            XCTAssertFalse(ChatVideoRules.isLive(state), state)
        }
    }

    /// `(pieces_done + frames_done) / (pieces + frames + 1)`, only while
    /// running, capped at 95%.
    func testTheBarIsTheHandoffsFormula() {
        let running = ChatVideoJob(
            id: "a", state: "running",
            progress: ChatVideoProgress(piecesDone: 18, pieces: 18, framesDone: 3, frames: 9)
        )
        XCTAssertEqual(21.0 / 28.0, ChatVideoRules.progress(running) ?? -1, accuracy: 0.0001)

        let early = ChatVideoJob(id: "b", state: "running", progress: ChatVideoProgress(piecesDone: 0, pieces: 0))
        XCTAssertEqual(0, ChatVideoRules.progress(early) ?? -1, accuracy: 0.0001, "the +1 is never a divide by zero")

        let last = ChatVideoJob(
            id: "c", state: "running",
            progress: ChatVideoProgress(piecesDone: 18, pieces: 18, framesDone: 9, frames: 9)
        )
        XCTAssertEqual(0.95, ChatVideoRules.progress(last) ?? -1, accuracy: 0.0001,
                       "still writing the summary: the bar does not read finished")

        XCTAssertNil(ChatVideoRules.progress(ChatVideoJob(id: "d", state: "queued")), "no bar while waiting")
        XCTAssertNil(ChatVideoRules.progress(ChatVideoJob(id: "e", state: "done")), "no bar once watched")
    }

    func testStopIsOfferedOnALiveJobNobodyHasStopped() {
        XCTAssertTrue(ChatVideoRules.stopOffered(ChatVideoJob(id: "a", state: "queued")))
        XCTAssertTrue(ChatVideoRules.stopOffered(ChatVideoJob(id: "a", state: "running")))
        XCTAssertFalse(
            ChatVideoRules.stopOffered(ChatVideoJob(id: "a", state: "running", cancelRequested: true)),
            "the server says Stopping… until it has; a second stop is a control that does nothing"
        )
        XCTAssertFalse(ChatVideoRules.stopOffered(ChatVideoJob(id: "a", state: "done")))
        XCTAssertTrue(ChatVideoRules.keptOffered(ChatVideoJob(id: "a", state: "done")))
        XCTAssertFalse(ChatVideoRules.keptOffered(ChatVideoJob(id: "a", state: "failed")))
        XCTAssertFalse(ChatVideoRules.keptOffered(ChatVideoJob(id: "a", state: "cancelled")))
    }

    func testAChipGoesUnderTheMessageItWasSentWith() {
        let jobs = [
            ChatVideoJob(id: "a", messageID: 10),
            ChatVideoJob(id: "b", messageID: 12),
            ChatVideoJob(id: "c", messageID: 10),
        ]
        XCTAssertEqual(["a", "c"], ChatVideoRules.jobs(jobs, under: 10).map(\.id))
        XCTAssertEqual([], ChatVideoRules.jobs(jobs, under: 11).map(\.id))
        XCTAssertEqual([], ChatVideoRules.jobs(jobs, under: nil).map(\.id), "a row with no id has no chip")
    }

    func testAThreadsVideosDecodeAndAnOlderServerHasNone() throws {
        let thread = try JSONDecoder().decode(ChatVideoThread.self, from: Data("""
        {"messages":[{"id":1,"role":"user","content":""}],"jobs":[],
         "videos":[{"id":"aa","message_id":1,"state":"done","line":"Watched"},{"state":"done"}]}
        """.utf8))
        XCTAssertEqual(["aa"], thread.videos.map(\.id), "a view with no id is nothing anyone can act on")
        let older = try JSONDecoder().decode(ChatVideoThread.self, from: Data(#"{"messages":[],"jobs":[]}"#.utf8))
        XCTAssertEqual([], older.videos.map(\.id))
    }

    // MARK: - what was kept

    func testTheResultsDecodeWithTheirMightShowWording() throws {
        let results = try JSONDecoder().decode(ChatVideoResults.self, from: Data("""
        {"job":{"id":"aa","state":"done","line":"Watched","summary":"Someone sings happy birthday."},
         "summary":"Someone sings happy birthday.",
         "transcript":[{"start_s":0.85,"end_s":9.1,"text":"happy birthday to you"},
                       {"start_s":9.1,"end_s":10.0,"text":"   "}],
         "frames":[{"at_s":1.0,"might_show":"a cake with candles on a table"},
                   {"at_s":2.0,"might_show":""}]}
        """.utf8))
        XCTAssertEqual("aa", results.job?.id)
        XCTAssertEqual("Someone sings happy birthday.", results.summary)
        XCTAssertEqual([ChatVideoSpoken(startS: 0.85, endS: 9.1, text: "happy birthday to you")], results.transcript)
        XCTAssertEqual([ChatVideoFrame(atS: 1.0, mightShow: "a cake with candles on a table")], results.frames)
        XCTAssertEqual(
            "might show: a cake with candles on a table",
            ChatVideoStrings.mightShow(results.frames[0].mightShow),
            "a frame is only ever a guess"
        )
        XCTAssertEqual("0:01", ChatVideoRules.clock(results.transcript[0].startS))
    }

    // MARK: - the send body and the paths

    private func object(_ data: Data) throws -> [String: Any] {
        try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func testTheSendBodyCarriesTheTokenOnlyWhenThereIsOne() throws {
        let with = try object(ChatPath.sendBody(content: "", deviceID: "ios-1", videoToken: "abc123"))
        XCTAssertEqual("", with["content"] as? String, "a video with no words is a message")
        XCTAssertEqual("ios-1", with["device_id"] as? String)
        XCTAssertEqual([["token": "abc123"]], with["videos"] as? [[String: String]])

        let without = try object(ChatPath.sendBody(content: "hello", deviceID: "ios-1"))
        XCTAssertNil(without["videos"], "no key at all, as before 1.4.0")
        let blank = try object(ChatPath.sendBody(content: "hello", deviceID: nil, videoToken: ""))
        XCTAssertNil(blank["videos"], "an empty token is no token")
    }

    func testThePaths() {
        XCTAssertEqual("/api/chat/videos?name=party.mp4", ChatPath.videoUpload(name: "party.mp4"))
        XCTAssertEqual(
            "/api/chat/videos?name=Ben%27s%20party%20%26%20cake%2B1.MOV",
            ChatPath.videoUpload(name: "Ben's party & cake+1.MOV"),
            "every byte outside URLEncoder's unreserved set is encoded, so the name arrives whole"
        )
        XCTAssertEqual("/api/chat/video-jobs/aa", ChatPath.videoJob("aa"))
        XCTAssertEqual("/api/chat/video-jobs/aa/results", ChatPath.videoJobResults("aa"))
        XCTAssertEqual("/api/chat/video-jobs/aa/cancel", ChatPath.videoJobCancel("aa"))
    }

    func testTheComposerSendsAReadyVideoWithABlankBox() {
        XCTAssertTrue(ChatVideoRules.sendOffered(typed: "", videoReady: true))
        XCTAssertTrue(ChatVideoRules.sendOffered(typed: "what is this?", videoReady: false))
        XCTAssertFalse(ChatVideoRules.sendOffered(typed: "   ", videoReady: false))
    }

    // MARK: - the spool a Photos clip is copied into

    func testAPhotosCopyKeepsItsNameAndIsDeletedByItsRelease() throws {
        let base = FileManager.default.temporaryDirectory
            .appendingPathComponent("chat-video-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: base) }
        let source = base.appendingPathComponent("picker-copy.MOV")
        try Data("a clip".utf8).write(to: source)

        let file = try ChatVideoSpool.copy(source, named: "../../IMG_0042.MOV", in: base)
        XCTAssertEqual("IMG_0042.MOV", file.name, "only the last path component: nothing a picker says points elsewhere")
        XCTAssertNil(file.mime)
        XCTAssertEqual("a clip", try String(contentsOf: file.url, encoding: .utf8))
        XCTAssertTrue(file.url.path.contains("/\(ChatVideoSpool.folder)/"))

        file.release()
        XCTAssertFalse(FileManager.default.fileExists(atPath: file.url.path), "release deletes the copy")
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: file.url.deletingLastPathComponent().path),
            "and its folder"
        )
        XCTAssertTrue(FileManager.default.fileExists(atPath: source.path), "never the picker's own file")
        file.release()   // a second call is harmless
    }

    func testAFilesReleaseRunsOnce() {
        let calls = Shared<Int>(0)
        let file = ChatVideoFile(url: URL(fileURLWithPath: "/tmp/party.mp4"), name: "party.mp4") {
            calls.mutate { $0 += 1 }
        }
        file.release()
        file.release()
        XCTAssertEqual(1, calls.current, "a security scope is closed once")
        XCTAssertNil(file.mime)
        XCTAssertEqual("video/webm", ChatVideoFile(url: URL(fileURLWithPath: "/tmp/x"), name: "x", mime: " video/webm ").mime)
    }
}
