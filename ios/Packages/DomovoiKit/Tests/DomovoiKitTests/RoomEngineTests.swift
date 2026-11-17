import Foundation
import XCTest
@testable import DomovoiKit

/// The room end to end on fakes (a core that signs, a socket the test speaks
/// for, a scripted microphone, ears that wake on a marked frame), with R1's
/// real ``WakeGate`` and ``CaptureMachine`` — and once on the real models,
/// when the build Mac has them.
final class RoomEngineTests: XCTestCase {

    private let core = RoomTestCore()
    private var engines: [RoomEngine] = []

    override func tearDown() {
        for e in engines { e.stop() }
        engines = []
        super.tearDown()
    }

    private struct Rig {
        let engine: RoomEngine
        let store: InMemoryRoomStore
        let transport: FakeRoomTransport
        let http: FakeRoomHTTP
        let source: ScriptedPCMSource
        let music: FakeRoomMusic
        let released: RoomLock<Int>
        let directory: URL
    }

    private func rig(
        echoCanceller: Bool = true,
        ears: any RoomEarsLoading = FakeRoomEarsLoader(),
        healthMS: Int64 = RoomEngine.healthIntervalMS,
        wakeWordsMax: Int = 5,
        script: (@Sendable (FakeRoomSocket) -> Void)? = nil
    ) -> Rig {
        let store = InMemoryRoomStore(
            enrolment: RoomEnrolment(coreURL: "http://192.168.1.30:6370", fingerprint: core.fingerprint, roomID: "kitchen", roomLabel: "Kitchen"),
            pairingToken: "ROOM-KEY", approvalCode: "111222"
        )
        let transport = FakeRoomTransport()
        transport.onOpen.withLock { $0 = script }
        // A core whose satellite media cache is empty: signed, empty lists.
        let http = FakeRoomHTTP.core(core, lists: [
            "/v1/wake-base/manifest.sig": core.envelope(channel: RoomIdentity.wakeBaseChannel, manifest: [:], serial: 1),
            "/v1/wake-models/manifest.sig": core.envelope(channel: RoomIdentity.wakeModelsChannel, manifest: [:], serial: 1),
            "/v1/sounds/manifest.sig": core.envelope(channel: RoomIdentity.soundsChannel, manifest: [:], serial: 1),
        ])
        let source = ScriptedPCMSource()
        let music = FakeRoomMusic()
        let released = RoomLock(0)
        let dir = RoomTestKit.tempDir()
        let engine = RoomEngine(parts: RoomEngineParts(
            store: store, ears: ears, pcm: { source }, device: FixedRoomDeviceFacts(), directory: dir,
            acquireMic: { { released.withLock { $0 += 1 } } }, micRefusal: { nil },
            http: http, transport: transport, wifi: FakeRoomWifi(),
            speaker: RoomSpeaker(output: RoomSilentSpeakerOutput()), music: music,
            echoCanceller: echoCanceller, wakeWordsMax: wakeWordsMax, healthIntervalMS: healthMS
        ))
        engines.append(engine)
        return Rig(engine: engine, store: store, transport: transport, http: http, source: source, music: music, released: released, directory: dir)
    }

    private static let readyFrame = #"{"type":"ready","protocol_version":"0.1","room_id":"kitchen","features":["speech_pause","end_capture","music_failed","health","log_push"]}"#

    private func waitFor(_ what: String, _ seconds: Double = 5, _ condition: () -> Bool, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(RoomTestKit.wait(seconds, condition), "never: \(what)", file: file, line: line)
    }

    // MARK: - the connection

    func testItProvesTheCoreSaysHelloAsAnIPhoneAndReportsItsSettingsOnReady() throws {
        let r = rig { s in s.say(Self.readyFrame) }
        XCTAssertTrue(r.engine.start())
        waitFor("ready") { r.engine.status.link == .ready }
        let socket = try XCTUnwrap(r.transport.last)
        XCTAssertEqual("ws://192.168.1.30:6370/v1/stream/kitchen", socket.url)
        XCTAssertTrue(r.http.asked.first?.hasPrefix("http://192.168.1.30:6370/v1/health?challenge=") == true, "\(r.http.asked)")
        waitFor("config_status") { socket.first("config_status") != nil }
        let hello = try XCTUnwrap(socket.first("hello"), "\(socket.texts)")
        XCTAssertEqual(.string("ios_room"), hello["board"])
        XCTAssertEqual(.string("ROOM-KEY"), hello["pairing_token"])
        XCTAssertEqual(.string("111222"), hello["approval_code"])
        XCTAssertEqual(.array(RoomProtocol.supports(echoCanceller: true).map { .string($0) }), hello["supports"])
        XCTAssertEqual(.int(100), socket.first("volume_status")?["level"])
        XCTAssertEqual(.double(0.5), socket.first("config_status")?["config"]?["wake.threshold"])
        XCTAssertTrue(r.store.approvedOnce)
        // The test core serves no models: the room says so, honestly.
        waitFor("wake problem") { r.engine.status.wakeProblem?.contains("no wake-word models yet") == true }
        XCTAssertFalse(r.engine.status.wakeReady)
    }

    func testAWrongKeyIsNeverDialled() throws {
        let r = rig()
        r.http.answer { [rogue = RoomTestCore()] url in url.contains("/v1/health?") ? Data(rogue.healthAnswer(url: url).utf8) : nil }
        XCTAssertTrue(r.engine.start())
        waitFor("refused") {
            if case .offline(let why) = r.engine.status.link { return why.contains("different server") }
            return false
        }
        XCTAssertTrue(r.transport.sockets.isEmpty, "no socket to a server that is not ours")
    }

    func testAParkedRoomShowsTheCoresCodeAndRefusalsSayWhatToDo() {
        let parked = rig { s in
            s.say(#"{"type":"error","reason":"awaiting_approval","message":"waiting","code":"482913"}"#)
            s.serverClose()
        }
        parked.engine.start()
        waitFor("awaiting approval") { parked.engine.status.link == .awaitingApproval(code: "482913", removed: false) }
        XCTAssertEqual("482913", parked.store.approvalCode)

        let rejected = rig { s in
            s.say(#"{"type":"error","reason":"pairing_rejected","message":"pairing rejected"}"#)
            s.serverClose()
        }
        rejected.engine.start()
        waitFor("rejected") { rejected.engine.status.link == .rejected(reason: RoomSessionRules.rejectedText) }

        let conflict = rig { s in
            s.say(#"{"type":"error","reason":"approval_conflict","message":"another"}"#)
            s.serverClose()
        }
        conflict.engine.start()
        waitFor("conflict") { conflict.engine.status.link == .conflict(reason: RoomSessionRules.conflictText) }
        // Parked rooms wait their 35 s (or a "check now", at most every 10 s).
        Thread.sleep(forTimeInterval: 0.5)
        XCTAssertEqual(1, parked.transport.sockets.count)
    }

    func testAPushWithNoUsableThresholdKeepsTheRoomsAndTheCoreHearsIt() throws {
        let r = rig { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        let socket = try XCTUnwrap(r.transport.last)
        waitFor("first config") { socket.all("config_status").count == 1 }
        socket.say(#"{"type":"set_wake_word","slug":"hey_house","threshold":0.62}"#)
        waitFor("second config") { socket.all("config_status").count == 2 }
        XCTAssertEqual(.double(0.62), socket.all("config_status").last?["config"]?["wake.threshold"])
        socket.say(#"{"type":"set_wake_word","slug":"hey_domovoi"}"#)
        waitFor("third config") { socket.all("config_status").count == 3 }
        XCTAssertEqual(.double(0.62), socket.all("config_status").last?["config"]?["wake.threshold"], "kept")
        XCTAssertEqual("hey_domovoi", r.store.wakeWord)
        XCTAssertEqual(0.62, r.store.wakeThreshold, accuracy: 0.0001)
        socket.say(#"{"type":"set_wake_word","slug":"../evil"}"#)
        Thread.sleep(forTimeInterval: 0.3)
        XCTAssertEqual(3, socket.all("config_status").count, "a bad slug changes nothing")
        XCTAssertEqual("hey_domovoi", r.store.wakeWord)
    }

    func testSetVolumeMovesTheRoomsOwnGainAndSaysSo() throws {
        let r = rig { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        let socket = try XCTUnwrap(r.transport.last)
        socket.say(#"{"type":"set_volume","level":40}"#)
        waitFor("volume_status 40") { socket.all("volume_status").last?["level"] == .int(40) }
    }

    // MARK: - drop-ins (220085d8)

    func testAnAwaitReadyDropinOpensAfterTheReplyThenSaysReady() throws {
        let r = rig { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        let socket = try XCTUnwrap(r.transport.last)
        socket.say(#"{"type":"response_start","text":"Connecting you with the office.","audio_sample_rate":16000}"#)
        socket.say(#"{"type":"dropin_start","peer_room":"office","peer_label":"Office","audio_sample_rate":16000,"full_duplex":true,"await_ready":true}"#)
        Thread.sleep(forTimeInterval: 0.3)
        XCTAssertNil(socket.first("dropin_ready"), "not while the announcement is still coming")
        XCTAssertNotEqual(.call, r.engine.status.phase)
        socket.events.binary(Data(count: 3_200))
        socket.say(#"{"type":"response_end","interrupted":false,"expect_followup":false}"#)
        waitFor("dropin_ready") { socket.first("dropin_ready") != nil }
        waitFor("in the call") { r.engine.status.phase == .call && r.engine.status.peerRoom == "Office" }
        // The listening thread reopens the microphone with voice processing.
        waitFor("call mic") { r.source.starts.last == true }
        socket.say(#"{"type":"dropin_end","reason":"hung_up"}"#)
        waitFor("call over") { r.engine.status.peerRoom == nil }
        waitFor("listening mic") { r.source.starts.last == false && r.source.starts.count >= 3 }
    }

    func testAPhoneWithoutAnEchoCancellerRefusesADropin() throws {
        let r = rig(echoCanceller: false) { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        let socket = try XCTUnwrap(r.transport.last)
        XCTAssertFalse(socket.first("hello")?["supports"]?.serialized.contains("full_duplex") ?? true)
        socket.say(#"{"type":"dropin_start","peer_room":"office","audio_sample_rate":16000,"full_duplex":true}"#)
        waitFor("dropin_end") { socket.first("dropin_end") != nil }
        XCTAssertNotEqual(.call, r.engine.status.phase)
    }

    // MARK: - the turn

    func testAWakeStreamsTheQuestionWithItsScoreAndWaitsForTheReply() throws {
        let r = rig { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        r.engine.useModels(RoomModelFiles(melspectrogram: r.directory, embedding: r.directory, vad: r.directory, wake: r.directory, wakeKey: "hey_jarvis"))
        waitFor("wake ready") { r.engine.status.wakeReady }
        let socket = try XCTUnwrap(r.transport.last)
        r.source.queue(FakeRoomEars.wakeMark, frames: 1)
        r.source.queue(2, frames: 30)
        r.source.queue(1_000, frames: 20)
        waitFor("utterance_start") { socket.first("utterance_start") != nil }
        let start = try XCTUnwrap(socket.first("utterance_start"))
        XCTAssertEqual(.string("wake_word"), start["trigger"])
        XCTAssertEqual(.double(0.912), start["wake_score"])
        waitFor("utterance_end", 10) { socket.first("utterance_end") != nil }
        let end = try XCTUnwrap(socket.first("utterance_end"))
        XCTAssertEqual(.string(CaptureExitReason.silence.rawValue), end["exit_reason"])
        XCTAssertEqual(.bool(false), end["greeting_played"])
        XCTAssertEqual(.bool(true), end["ack_before_capture"])
        XCTAssertEqual(start["utt"], end["utt"])
        XCTAssertNotNil(socket.first("speech_pause"), "the core's pause hint")
        XCTAssertGreaterThan(socket.binaries, 20)
        XCTAssertEqual(.thinking, r.engine.status.phase)
        socket.say(#"{"type":"response_start","audio_sample_rate":22050}"#)
        socket.events.binary(Data(count: 4_410))
        socket.say(#"{"type":"response_end","interrupted":false,"expect_followup":false}"#)
        waitFor("listening again") { r.engine.status.phase == .listening }
        XCTAssertFalse(r.engine.status.micLive)
    }

    func testAWakeWithNoServerStreamsNothing() throws {
        let r = rig { s in
            s.say(#"{"type":"error","reason":"awaiting_approval","code":"482913"}"#)
            s.serverClose()
        }
        r.engine.start()
        waitFor("parked") { if case .awaitingApproval = r.engine.status.link { return true }; return false }
        r.engine.useModels(RoomModelFiles(melspectrogram: r.directory, embedding: r.directory, vad: r.directory, wake: r.directory, wakeKey: "hey_jarvis"))
        waitFor("wake ready") { r.engine.status.wakeReady }
        r.source.queue(FakeRoomEars.wakeMark, frames: 1)
        waitFor("wake consumed") { r.source.queued == 0 }
        Thread.sleep(forTimeInterval: 0.3)
        XCTAssertNil(r.transport.last?.first("utterance_start"))
        XCTAssertEqual(.listening, r.engine.status.phase)
    }

    // MARK: - mute, stop, health

    func testMuteClosesTheMicrophoneAndStopGivesItBack() throws {
        let r = rig { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("mic open") { r.engine.status.micOpen }
        r.engine.setMuted(true)
        waitFor("mic closed") { !r.engine.status.micOpen }
        XCTAssertTrue(r.store.muted)
        XCTAssertTrue(r.engine.status.muted)
        r.engine.setMuted(false)
        waitFor("mic open again") { r.engine.status.micOpen }
        let socket = try XCTUnwrap(r.transport.last)
        r.engine.stop()
        XCTAssertTrue(socket.isClosed)
        XCTAssertEqual(1, r.released.withLock { $0 })
        XCTAssertFalse(r.engine.status.micOpen)
        // Started again, it dials again.
        XCTAssertTrue(r.engine.start())
        waitFor("a second socket") { r.transport.sockets.count == 2 }
    }

    func testAnUnenrolledPhoneDoesNotStartAndTheMicIsNeverTaken() {
        let taken = RoomLock(0)
        let engine = RoomEngine(parts: RoomEngineParts(
            store: InMemoryRoomStore(), ears: FakeRoomEarsLoader(), pcm: { ScriptedPCMSource() }, device: FixedRoomDeviceFacts(),
            directory: RoomTestKit.tempDir(), acquireMic: { taken.withLock { $0 += 1 }; return {} }, micRefusal: { nil },
            http: FakeRoomHTTP(), transport: FakeRoomTransport(), wifi: FakeRoomWifi(),
            speaker: RoomSpeaker(output: RoomSilentSpeakerOutput()), music: FakeRoomMusic(), echoCanceller: true
        ))
        XCTAssertFalse(engine.start())
        XCTAssertEqual(.offline(reason: "this phone is not set up as a room"), engine.status.link)
        XCTAssertEqual(0, taken.withLock { $0 })
    }

    func testARefusedMicrophoneSaysWhose() {
        let engine = RoomEngine(parts: RoomEngineParts(
            store: InMemoryRoomStore(enrolment: RoomEnrolment(coreURL: "http://192.168.1.30:6370", fingerprint: core.fingerprint, roomID: "kitchen", roomLabel: "Kitchen"),
                                     pairingToken: "k"),
            ears: FakeRoomEarsLoader(), pcm: { ScriptedPCMSource() }, device: FixedRoomDeviceFacts(),
            directory: RoomTestKit.tempDir(), acquireMic: { nil }, micRefusal: { "A drop-in call has the microphone." },
            http: FakeRoomHTTP(), transport: FakeRoomTransport(), wifi: FakeRoomWifi(),
            speaker: RoomSpeaker(output: RoomSilentSpeakerOutput()), music: FakeRoomMusic(), echoCanceller: true
        ))
        XCTAssertFalse(engine.start())
        XCTAssertEqual(.offline(reason: "A drop-in call has the microphone."), engine.status.link)
    }

    func testTheHealthFrameGoesOnlyToACoreThatAskedAndCarriesTheBattery() throws {
        let r = rig(healthMS: 200) { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        let socket = try XCTUnwrap(r.transport.last)
        waitFor("health", 5) { socket.first("health") != nil }
        let health = try XCTUnwrap(socket.first("health"))
        XCTAssertEqual(.object(["percent": .int(80), "charging": .bool(true), "on_power": .bool(true)]), health["battery"])
        XCTAssertEqual(.string("up"), health["ws"])
        XCTAssertNil(health["wifi"])

        let quiet = rig(healthMS: 200) { s in s.say(#"{"type":"ready","features":["speech_pause"]}"#) }
        quiet.engine.start()
        waitFor("ready") { quiet.engine.status.link == .ready }
        Thread.sleep(forTimeInterval: 0.7)
        XCTAssertNil(quiet.transport.last?.first("health"), "an older core is never sent a frame it would refuse")
    }

    func testStatusUpdatesStartWithTheStatusAndFinishWhenTheRoomStops() async throws {
        let r = rig { s in s.say(Self.readyFrame) }
        let stream = r.engine.statusUpdates()
        r.engine.start()
        var sawReady = false
        for await s in stream {
            if s.link == .ready {
                sawReady = true
                r.engine.stop()
            }
        }
        XCTAssertTrue(sawReady)
    }

    // MARK: - several wake words (1.4.0)

    /// The room's three words: porch_cat with its own 0.95, hey_house at the
    /// room's 0.5.
    private static let threeWords = #"{"type":"set_wake_word","slug":"hey_jarvis","threshold":0.5,"words":[{"slug":"hey_jarvis","threshold":0.5},{"slug":"porch_cat","threshold":0.95},{"slug":"hey_house"}]}"#

    private func modelFiles(_ r: Rig, _ main: String, _ extras: [String]) -> RoomModelFiles {
        RoomModelFiles(melspectrogram: r.directory, embedding: r.directory, vad: r.directory, wake: r.directory, wakeKey: main,
                       extras: extras.map { RoomWakeModelFile(slug: $0, file: r.directory) })
    }

    /// Says `frame` as the core, hands the room the models a verified sync
    /// would, and returns the next session (ready), whose hello lists what
    /// loaded.
    private func pushWords(_ r: Rig, _ frame: String, loads main: String, _ extras: [String]) throws -> FakeRoomSocket {
        let before = r.transport.sockets.count
        let socket = try XCTUnwrap(r.transport.last)
        let configs = socket.all("config_status").count
        socket.say(frame)
        waitFor("config_status after the push") { socket.all("config_status").count == configs + 1 }
        r.engine.useModels(modelFiles(r, main, extras))
        waitFor("a new session") { r.transport.sockets.count == before + 1 }
        let next = try XCTUnwrap(r.transport.last)
        waitFor("ready again") { next.first("config_status") != nil }
        return next
    }

    func testAPushOfSeveralWordsLoadsThemAndANewHelloListsThem() throws {
        let r = rig(ears: FakeWordsEarsLoader()) { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        let first = try XCTUnwrap(r.transport.last)
        let hello = try XCTUnwrap(first.first("hello"))
        XCTAssertEqual(.array([.string("hey_jarvis")]), hello["wake_words"])
        XCTAssertEqual(.int(5), hello["wake_words_max"])

        let next = try pushWords(r, Self.threeWords, loads: "hey_jarvis", ["porch_cat", "hey_house"])
        XCTAssertTrue(first.isClosed, "the old session ends for the new hello")
        let again = try XCTUnwrap(next.first("hello"))
        XCTAssertEqual(.array(["hey_jarvis", "porch_cat", "hey_house"].map { .string($0) }), again["wake_words"])
        XCTAssertEqual(.int(5), again["wake_words_max"])
        XCTAssertEqual(.string("hey_jarvis"), again["wake_word"])
        XCTAssertEqual([RoomWakeWord(slug: "porch_cat", threshold: 0.95), RoomWakeWord(slug: "hey_house")], r.store.wakeExtras)
        XCTAssertEqual(.double(0.5), next.first("config_status")?["config"]?["wake.threshold"])
        // Settled: the hello says what loaded, so no third session.
        Thread.sleep(forTimeInterval: 0.3)
        XCTAssertEqual(2, r.transport.sockets.count)
    }

    func testTheWordThatFiredIsTheTurnsWakeWordAndTheFollowUpHasNone() throws {
        let r = rig(ears: FakeWordsEarsLoader()) { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        let socket = try pushWords(r, Self.threeWords, loads: "hey_jarvis", ["porch_cat", "hey_house"])
        r.source.queue(FakeWordsEars.mark(2), frames: 1)
        r.source.queue(2, frames: 30)
        r.source.queue(1_000, frames: 20)
        waitFor("utterance_start") { socket.first("utterance_start") != nil }
        let start = try XCTUnwrap(socket.first("utterance_start"))
        XCTAssertEqual(.string("wake_word"), start["trigger"])
        XCTAssertEqual(.string("hey_house"), start["wake_word"])
        XCTAssertEqual(.double(0.9), start["wake_score"])
        waitFor("utterance_end", 10) { socket.first("utterance_end") != nil }
        socket.say(#"{"type":"response_start","audio_sample_rate":22050}"#)
        socket.events.binary(Data(count: 4_410))
        socket.say(#"{"type":"response_end","interrupted":false,"expect_followup":true}"#)
        waitFor("the follow-up's utterance_start", 10) { socket.all("utterance_start").count == 2 }
        let followup = socket.all("utterance_start")[1]
        XCTAssertEqual(.string("followup"), followup["trigger"])
        XCTAssertNil(followup["wake_word"], "a follow-up was not woken by a word")
        XCTAssertNil(followup["wake_score"])
    }

    func testEachWordWakesAtItsOwnThreshold() throws {
        let r = rig(ears: FakeWordsEarsLoader()) { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        let socket = try pushWords(r, Self.threeWords, loads: "hey_jarvis", ["porch_cat", "hey_house"])
        // porch_cat scores 0.9, under its own 0.95.
        r.source.queue(FakeWordsEars.mark(1), frames: 1)
        waitFor("heard") { r.source.queued == 0 }
        Thread.sleep(forTimeInterval: 0.3)
        XCTAssertNil(socket.first("utterance_start"), "porch_cat wakes only over its own threshold")
        // The main word's 0.9 is over the room's 0.5.
        r.source.queue(FakeWordsEars.mark(0), frames: 1)
        waitFor("utterance_start") { socket.first("utterance_start") != nil }
        XCTAssertEqual(.string("hey_jarvis"), socket.first("utterance_start")?["wake_word"])
    }

    func testAPushWithoutWordsRunsTheSlugAloneAndDropsTheExtras() throws {
        let r = rig(ears: FakeWordsEarsLoader()) { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        _ = try pushWords(r, Self.threeWords, loads: "hey_jarvis", ["porch_cat", "hey_house"])
        XCTAssertEqual(2, r.store.wakeExtras.count)
        let last = try pushWords(r, #"{"type":"set_wake_word","slug":"hey_jarvis"}"#, loads: "hey_jarvis", [])
        XCTAssertEqual([], r.store.wakeExtras)
        XCTAssertEqual(["hey_jarvis"], r.engine.configuredWakeWords())
        XCTAssertEqual(.array([.string("hey_jarvis")]), last.first("hello")?["wake_words"])
        XCTAssertEqual(3, r.transport.sockets.count)
    }

    func testTheRoomRunsNoMoreThanItsOwnLimit() throws {
        let r = rig(ears: FakeWordsEarsLoader(), wakeWordsMax: 3) { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        XCTAssertEqual(.int(3), r.transport.last?.first("hello")?["wake_words_max"])
        let five = #"{"type":"set_wake_word","slug":"hey_jarvis","words":[{"slug":"hey_jarvis"},{"slug":"porch_cat"},{"slug":"hey_house"},{"slug":"alexa"},{"slug":"timer"}]}"#
        let next = try pushWords(r, five, loads: "hey_jarvis", ["porch_cat", "hey_house"])
        XCTAssertEqual(["porch_cat", "hey_house"], r.store.wakeExtras.map(\.slug))
        XCTAssertEqual(["hey_jarvis", "porch_cat", "hey_house"], r.engine.configuredWakeWords())
        XCTAssertEqual(.array(["hey_jarvis", "porch_cat", "hey_house"].map { .string($0) }), next.first("hello")?["wake_words"])
        XCTAssertEqual(.int(3), next.first("hello")?["wake_words_max"])
    }

    /// An extra word whose model did not load is left out of the hello.
    func testTheHelloListsOnlyTheWordsThatLoaded() throws {
        let r = rig(ears: FakeWordsEarsLoader()) { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        let next = try pushWords(r, Self.threeWords, loads: "hey_jarvis", ["hey_house"])
        XCTAssertEqual(.array([.string("hey_jarvis"), .string("hey_house")]), next.first("hello")?["wake_words"])
        XCTAssertEqual(2, r.store.wakeExtras.count, "the room keeps the list; the server hears what runs")
    }

    func testEveryWordListensDuringADropin() throws {
        let r = rig(ears: FakeWordsEarsLoader()) { s in s.say(Self.readyFrame) }
        r.engine.start()
        waitFor("ready") { r.engine.status.link == .ready }
        let socket = try pushWords(r, Self.threeWords, loads: "hey_jarvis", ["porch_cat", "hey_house"])
        socket.say(#"{"type":"dropin_start","peer_room":"office","peer_label":"Office","audio_sample_rate":16000,"full_duplex":true}"#)
        waitFor("in the call") { r.engine.status.phase == .call }
        waitFor("call mic") { r.source.starts.last == true }
        // porch_cat under its own threshold is relayed, not heard.
        r.source.queue(FakeWordsEars.mark(1), frames: 1)
        waitFor("relayed") { r.source.queued == 0 }
        Thread.sleep(forTimeInterval: 0.2)
        XCTAssertNil(socket.first("utterance_start"))
        r.source.queue(FakeWordsEars.mark(2), frames: 1)
        waitFor("the command's utterance_start") { socket.first("utterance_start") != nil }
        let start = try XCTUnwrap(socket.first("utterance_start"))
        XCTAssertEqual(.string("wake_word"), start["trigger"])
        XCTAssertEqual(.string("hey_house"), start["wake_word"])
        XCTAssertEqual(2, r.transport.sockets.count, "no new hello in the middle of a call")
    }

    // MARK: - the real models (the build Mac's fixtures)

    func testHeyJarvisOnTheRealModelsWakesTheRoomAndStreamsTheQuestion() throws {
        let root = try RoomFixtures.require()
        let r = rig(ears: OnnxRoomEarsLoader()) { s in s.say(Self.readyFrame) }
        let models = r.directory.appendingPathComponent("models")
        try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
        for name in RoomFixtures.models {
            try FileManager.default.copyItem(at: RoomFixtures.model(root, name), to: models.appendingPathComponent(name))
        }
        r.engine.start()
        waitFor("ready", 10) { r.engine.status.link == .ready }
        // The models on disk are the pinned ones, so the room loads them itself.
        waitFor("the real ears", 20) { r.engine.status.wakeReady }
        // The audio only once the room is listening.
        r.source.queue(samples: RoomFixtures.padded(try RoomFixtures.readWav(RoomFixtures.clip(root, "hey_jarvis.wav"))))
        r.source.queue(samples: try RoomFixtures.readWav(RoomFixtures.clip(root, "what_time.wav")))
        r.source.queue(samples: [Int16](repeating: 0, count: 32_000))
        let socket = try XCTUnwrap(r.transport.last)
        waitFor("utterance_start", 30) { socket.first("utterance_start") != nil }
        let start = try XCTUnwrap(socket.first("utterance_start"))
        if case .double(let score)? = start["wake_score"] {
            XCTAssertGreaterThan(score, Double(RoomWakeSettings.defaultThreshold))
        } else {
            XCTFail("no wake_score: \(start)")
        }
        waitFor("utterance_end", 30) { socket.first("utterance_end") != nil }
        XCTAssertGreaterThan(socket.first("utterance_end")?["voiced_frames"].flatMap { if case .int(let n) = $0 { return Int(n) }; return nil } ?? 0, 10)
    }
}
