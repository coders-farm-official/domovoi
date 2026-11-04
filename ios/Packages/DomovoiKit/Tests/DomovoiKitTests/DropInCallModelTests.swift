import Foundation
import XCTest
@testable import DomovoiKit

/// The 2026-11-03 Rooms batch's package tests: the phone's drop-in call (D54)
/// and the two ``RoomsModel`` changes a pushed room screen needed (D56).
///
/// **One file for both, because the batch gave this unit one test file.** The
/// ownership list is `DropInCallModelTests.swift` and nothing else under
/// `Tests/`, and `RoomsModelTests.swift` belongs to the night's Rooms item;
/// two units editing it is the merge conflict BATCH-RULES §1 is about. The
/// `RoomsModel` cases are in their own section at the foot, named for what
/// they are about, and DECISIONS records the arrangement.
///
/// Nothing here touches a real microphone, a real socket or a real
/// `AVAudioSession`: ``RecordingDropInAudio``, ``FakeDropInTransport`` and
/// ``RecordingPlayerAudioSession`` are what the model is driven against, which
/// is the split ``DropInAudio`` exists for.
@MainActor
final class DropInCallModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let otherServer = "http://10.0.0.99:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// A transport that opens nothing and hands the test the link's two ends.
    private final class FakeDropInTransport: DropInSocketTransport {

        let opened = Shared<[URLRequest]>([])
        let events = Shared<DropInSocketEvents?>(nil)
        let link = FakeDropInSocket()

        func open(_ request: URLRequest, reporting events: DropInSocketEvents) -> any DropInSocket {
            opened.mutate { $0.append(request) }
            self.events.set(events)
            return link
        }

        /// The request's device-token header, which is what says the upgrade
        /// was minted rather than built by hand.
        var token: String? {
            opened.current.last?.value(forHTTPHeaderField: deviceTokenHeader)
        }

        var address: String? { opened.current.last?.url?.absoluteString }
    }

    private final class FakeDropInSocket: DropInSocket {
        let binary = Shared<[Data]>([])
        let text = Shared<[String]>([])
        let closes = Shared(0)

        func sendBinary(_ data: Data) { binary.mutate { $0.append(data) } }
        func sendText(_ text: String) { self.text.mutate { $0.append(text) } }
        func close() { closes.mutate { $0 += 1 } }
    }

    /// A house that answers the identity probe and `phone-info`.
    private final class House: Sendable {

        /// What `GET …/dropin/phone-info` says. `domovoi_host: null` is the
        /// real route's answer for a core beside the web backend
        /// (`satellites.py:1549-1551`).
        let phoneInfo = Shared(#"{"room_id":"kitchen","domovoi_host":null,"domovoi_port":6370,"ws_path":"/v1/dropin/kitchen","audio_sample_rate":16000}"#)
        let phoneInfoStatus = Shared(200)

        /// Run just before `phone-info` answers, so a test can move house
        /// **inside** the round trip — which is the only way to test the
        /// read-after-await rule (`NIGHT-RULES` §3).
        let duringTheRead = Shared<(@Sendable () -> Void)?>(nil)

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url,
                      let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                if parts.path == "/api/health" {
                    let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                }
                if parts.path.hasSuffix("/dropin/phone-info") {
                    duringTheRead.current?()
                    guard phoneInfoStatus.current == 200 else {
                        return .json(#"{"detail":"no such room"}"#, status: phoneInfoStatus.current)
                    }
                    return .json(phoneInfo.current)
                }
                if parts.path == "/api/satellites" { return .json("[]") }
                return .json("{}")
            }
        }
    }

    private struct Rig {
        let model: DropInCallModel
        let house: House
        let transport: FakeDropInTransport
        let audio: RecordingDropInAudio
        let session: RecordingPlayerAudioSession
        let store: InMemoryShellStore
    }

    private func rig(
        micAllowed: Bool = true,
        audio: RecordingDropInAudio = RecordingDropInAudio(),
        phoneID: String? = "ios-7f3a"
    ) -> Rig {
        let house = House()
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let transport = FakeDropInTransport()
        let session = RecordingPlayerAudioSession()
        let model = DropInCallModel(
            client: container.client,
            transport: transport,
            audio: audio,
            session: session,
            permission: FixedMicPermission(micAllowed),
            phoneID: phoneID,
            // The clock never ticks unless a test wants it to: `elapsedSec`
            // is not what any of these are about, and a real second in a
            // package test is a second in every run.
            tick: { _ in try await Task.sleep(for: .seconds(3_600)) }
        )
        return Rig(model: model, house: house, transport: transport,
                   audio: audio, session: session, store: store)
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
        XCTFail("\(what) never happened in \(settleYieldLimit) yields", file: file, line: line)
    }

    /// Open a call and get it to `live`, which most of these start from.
    private func liveCall(_ rig: Rig, room: String = "kitchen") async {
        await rig.model.start(room: room)
        await settleUntil({ rig.transport.events.current != nil }, "the socket opened")
        rig.transport.events.current?.opened()
        rig.transport.events.current?.text(#"{"type":"dropin_start","peer_room":"kitchen"}"#)
        await settleUntil({ rig.model.state.isLive }, "the call went live")
    }

    // MARK: - the address

    /// A null `domovoi_host` means "the host you reached us on"
    /// (`satellites.py:1549-1551`), and the port is the core's.
    func testTheSocketGoesToTheCoresPortOnTheHouseHost() {
        let url = dropInSocketURL(
            house: "http://10.0.0.42:6369",
            info: DropInPhoneInfo(domovoiHost: nil, domovoiPort: 6370),
            room: "kitchen",
            phoneID: "ios-7f3a"
        )
        XCTAssertEqual(url, "ws://10.0.0.42:6370/v1/dropin/kitchen?phone_id=ios-7f3a")
    }

    /// An `https` house is a `wss` socket (`DropinCallClient.kt:150`), and a
    /// host the read names wins over the one the phone reached.
    func testHttpsBecomesWssAndANamedHostIsUsed() {
        let url = dropInSocketURL(
            house: "https://domovoi.lan",
            info: DropInPhoneInfo(domovoiHost: "core.lan", domovoiPort: nil),
            room: "front_room",
            phoneID: nil
        )
        // No port in the read: the core's own default, 6370
        // (`satellites.py:1552`). And no `phone_id`, because there is no
        // device id to give — the core then uses its own `phone`.
        XCTAssertEqual(url, "wss://core.lan:6370/v1/dropin/front_room")
    }

    /// The one case Android papers over with `?: "localhost"`: a house address
    /// that does not parse has no socket, and saying so beats connecting to
    /// this device.
    func testAnUnparseableHouseHasNoSocket() {
        XCTAssertNil(
            dropInSocketURL(house: "", info: DropInPhoneInfo(), room: "kitchen", phoneID: "a")
        )
    }

    // MARK: - starting a call

    /// The happy path: the mic is taken, the session is `.playAndRecord`, the
    /// engine is open, the upgrade carries the household token, and the
    /// server's `dropin_start` makes it live.
    func testACallTakesTheMicOpensTheEngineAndCarriesTheToken() async {
        let rig = rig()
        await liveCall(rig)

        XCTAssertEqual(rig.model.state, .live(peer: "kitchen"))
        XCTAssertEqual(rig.audio.starts, 1, "the engine was never opened")
        XCTAssertEqual(
            rig.session.activations,
            [.recording],
            "the audio session was not put into `.playAndRecord` before the engine"
        )
        XCTAssertEqual(
            PlayerMicArbiter.shared.holder(),
            .dropIn,
            "the call did not take the microphone through the arbiter"
        )
        XCTAssertEqual(
            rig.transport.token,
            "household-abc",
            "the upgrade went out without the household device token, so the "
                + "core would close it 1008 (`main.py:7093-7100`)"
        )
        XCTAssertEqual(
            rig.transport.address,
            "ws://10.0.0.42:6370/v1/dropin/kitchen?phone_id=ios-7f3a"
        )
    }

    /// A microphone somebody else holds is a **sentence**, not a silent
    /// failure (`MicArbiter.kt:64`, and the arbiter's own doc comment).
    func testTheMicBeingTakenIsASentenceAndNothingIsOpened() async {
        let rig = rig()
        // Room mode has it, which is the exclusive owner.
        let held = PlayerMicArbiter.tryAcquire(.roomMode)
        XCTAssertNotNil(held)

        await rig.model.start(room: "kitchen")

        XCTAssertEqual(rig.model.state, .failed(note: PlayerStrings.micHeldByRoomMode))
        XCTAssertEqual(rig.audio.starts, 0, "the engine was opened although the mic was refused")
        XCTAssertTrue(rig.transport.opened.current.isEmpty, "a socket was opened anyway")
        XCTAssertTrue(
            rig.session.activations.isEmpty,
            "the audio session was activated although there was no microphone to use"
        )
    }

    /// A refused microphone permission says where to turn it on, and takes
    /// nothing (`PhoneDropinCall.kt:78` toasts the same fact).
    func testARefusedPermissionSaysWhereToTurnItOn() async {
        let rig = rig(micAllowed: false)
        await rig.model.start(room: "kitchen")

        XCTAssertEqual(rig.model.state, .failed(note: DropInStrings.micRefused))
        XCTAssertNil(
            PlayerMicArbiter.shared.holder(),
            "the arbiter's lease was taken before the person had said yes"
        )
        XCTAssertTrue(rig.transport.opened.current.isEmpty)
    }

    /// An engine that will not open gives the microphone **back**.
    ///
    /// The lease is the thing a leak is invisible in: nothing else in the app
    /// would be able to record again, and no screen would say why.
    func testAnEngineThatWillNotOpenHandsTheMicBack() async {
        let refusing = RecordingDropInAudio(refusing: DropInAudioFailed(note: "no input device"))
        let rig = rig(audio: refusing)
        await rig.model.start(room: "kitchen")

        XCTAssertEqual(rig.model.state, .failed(note: "no input device"))
        XCTAssertNil(PlayerMicArbiter.shared.holder(), "the microphone was left held")
        XCTAssertEqual(
            rig.session.categories.last,
            .music,
            "the audio session was left in `.playAndRecord` after a failed start"
        )
        XCTAssertTrue(rig.transport.opened.current.isEmpty, "a socket was opened with no audio")
    }

    /// **The read-after-await rule.** The phone moves house while
    /// `phone-info` is in flight, and the answer — the *old* house's core
    /// address — must not open a socket.
    ///
    /// Both houses can have a `kitchen`, so this is not a theoretical
    /// mix-up: without the check the phone would open the previous house's
    /// microphone from a screen showing the new one's rooms. The switch is
    /// made **inside** the round trip, which is the only place it tests
    /// anything.
    func testAHouseChangedDuringTheReadOpensNoSocket() async {
        let rig = rig()
        let store = rig.store
        rig.house.duringTheRead.set { @Sendable in
            _ = store.setServerURL("http://10.0.0.99:6369")
        }

        await rig.model.start(room: "kitchen")

        XCTAssertTrue(
            rig.transport.opened.current.isEmpty,
            "the old house's core address opened a socket after the phone moved"
        )
        XCTAssertEqual(rig.model.state, .idle, "a house move is not a failure to report")
        XCTAssertNil(PlayerMicArbiter.shared.holder(), "the microphone was left held")
        XCTAssertEqual(rig.audio.starts, 0)
    }

    /// `phone-info` is device tier and can refuse. One sentence, and nothing
    /// held.
    func testAPhoneInfoRefusalSaysSoAndHoldsNothing() async {
        let rig = rig()
        rig.house.phoneInfoStatus.set(403)

        await rig.model.start(room: "kitchen")

        XCTAssertEqual(rig.model.state, .failed(note: DropInStrings.cannotReachTheServer))
        XCTAssertNil(PlayerMicArbiter.shared.holder())
        XCTAssertEqual(rig.audio.starts, 0)
    }

    /// A second `start` while a call is on is ignored
    /// (`DropinCallClient.kt:72`).
    func testASecondStartDuringACallDoesNothing() async {
        let rig = rig()
        await liveCall(rig)

        await rig.model.start(room: "study")

        XCTAssertEqual(rig.transport.opened.current.count, 1, "a second socket was opened")
        XCTAssertEqual(rig.model.state, .live(peer: "kitchen"))
        XCTAssertEqual(rig.model.room, "kitchen", "the second start took the screen's room over")
    }

    // MARK: - heard, and heard back

    /// The microphone's frames go up verbatim, and the room's audio comes
    /// down to the speaker. Both directions, because a call that only works
    /// one way is the failure nobody notices until somebody is in the room.
    func testTheMicGoesUpAndTheRoomsAudioComesDown() async {
        let rig = rig()
        await liveCall(rig)

        let frame = Data(repeating: 7, count: DropInAudioFormat.frameBytes)
        rig.audio.feed(frame)
        XCTAssertEqual(
            rig.transport.link.binary.current,
            [frame],
            "the captured frame did not reach the socket"
        )

        let fromTheRoom = Data(repeating: 3, count: 480)
        rig.transport.events.current?.binary(fromTheRoom)
        XCTAssertEqual(
            rig.audio.played,
            [fromTheRoom],
            "the room's relayed audio and its chimes were not played"
        )
    }

    /// `mute` keeps the microphone open and sends nothing
    /// (`DropinCallClient.kt:205`): the engine is told, and nothing is
    /// stopped.
    func testMutingTellsTheEngineAndKeepsTheMicOpen() async {
        let rig = rig()
        await liveCall(rig)

        rig.model.setMuted(true)
        XCTAssertTrue(rig.model.muted)
        XCTAssertEqual(rig.audio.muteCalls, [true])
        XCTAssertTrue(rig.audio.open, "muting closed the microphone")

        rig.model.setMuted(false)
        XCTAssertEqual(rig.audio.muteCalls, [true, false])
    }

    // MARK: - the ring, which Android has no case for

    /// The server's ring mode parks the call and rings the room first
    /// (`phone_dropin.py:26-32`), and this phone says so.
    ///
    /// Android handles no `dropin_ringing` frame at all, so over there a rung
    /// room sits on `connecting…` until the ring timeout and then reports
    /// `no_answer`. One more for `ANDROID-DEFECTS-FOUND-BY-IOS.md`.
    func testARungRoomSaysItIsRingingAndThenGoesLive() async {
        let rig = rig()
        await rig.model.start(room: "kitchen")
        await settleUntil({ rig.transport.events.current != nil }, "the socket opened")

        rig.transport.events.current?.text(#"{"type":"dropin_ringing","peer_room":"kitchen"}"#)
        await settleUntil({ rig.model.state == .ringing(peer: "kitchen") }, "the ring was read")
        XCTAssertTrue(rig.audio.open, "the microphone was closed while the room was ringing")

        rig.transport.events.current?.text(#"{"type":"dropin_start","peer_room":"kitchen"}"#)
        await settleUntil({ rig.model.state.isLive }, "the answered call went live")
        XCTAssertEqual(rig.audio.starts, 1, "the engine was restarted when the room answered")
    }

    /// A room that never answers ends the call with the server's reason.
    func testAnUnansweredRingEndsWithTheServersReason() async {
        let rig = rig()
        await rig.model.start(room: "kitchen")
        await settleUntil({ rig.transport.events.current != nil }, "the socket opened")
        rig.transport.events.current?.text(#"{"type":"dropin_ringing","peer_room":"kitchen"}"#)
        await settleUntil({ rig.model.state == .ringing(peer: "kitchen") }, "the ring was read")

        rig.transport.events.current?.text(#"{"type":"dropin_end","reason":"no_answer"}"#)
        await settleUntil({ rig.model.state == .ended(reason: "no_answer") }, "the end was read")
        XCTAssertFalse(rig.audio.open, "the microphone was left open after the call ended")
        XCTAssertNil(PlayerMicArbiter.shared.holder())
    }

    // MARK: - refusals and endings

    /// Each of the core's feasibility codes is a sentence
    /// (`DropinCallClient.kt:247-254`), and an unknown one still reaches the
    /// person rather than being flattened.
    func testEachRefusalCodeIsASentence() async {
        for (code, words) in [
            ("target_offline", "that room isn't connected"),
            ("target_no_aec", "that room's mic can't do drop-in (needs the XVF3800 array)"),
            ("target_busy", "that room is already in a call"),
            ("initiator_busy", "this phone is already in a call"),
            ("disabled", "drop-in is turned off in settings"),
            ("something_new", "refused: something_new"),
        ] {
            XCTAssertEqual(DropInStrings.refusal(code), words, "`\(code)`")
        }
    }

    /// An `error` frame ends the call, hands the microphone back and says
    /// what the house said.
    func testAnErrorFrameEndsTheCallAndFreesTheMic() async {
        let rig = rig()
        await rig.model.start(room: "kitchen")
        await settleUntil({ rig.transport.events.current != nil }, "the socket opened")

        rig.transport.events.current?.text(#"{"type":"error","code":"target_busy"}"#)
        await settleUntil(
            { rig.model.state == .failed(note: "that room is already in a call") },
            "the refusal was read"
        )
        XCTAssertFalse(rig.audio.open, "the microphone was left open after a refusal")
        XCTAssertNil(PlayerMicArbiter.shared.holder())
        XCTAssertEqual(rig.transport.link.closes.current, 1, "the socket was left open")
        XCTAssertEqual(rig.session.categories.last, .music)
    }

    /// `hang up` tells the room first (`DropinCallClient.kt:217`), then puts
    /// everything down.
    func testHangingUpTellsTheRoomThenPutsEverythingDown() async {
        let rig = rig()
        await liveCall(rig)

        rig.model.hangUp()

        XCTAssertEqual(
            rig.transport.link.text.current,
            [#"{"type":"dropin_end"}"#],
            "the room was not told the call was over"
        )
        XCTAssertEqual(rig.model.state, .ended(reason: "ended"))
        XCTAssertFalse(rig.audio.open)
        XCTAssertNil(PlayerMicArbiter.shared.holder())
        XCTAssertEqual(rig.session.categories.last, .music)
        XCTAssertEqual(
            rig.session.deactivations,
            0,
            "the session was deactivated rather than put back to `.playback`, "
                + "which takes it off whatever picked the audio up during the call"
        )
    }

    /// A frame that arrives from the socket **after** the hang-up changes
    /// nothing, which is the generation check.
    func testALateFrameFromAHungUpCallIsIgnored() async {
        let rig = rig()
        await liveCall(rig)
        let events = rig.transport.events.current
        rig.model.hangUp()

        events?.failed(500)
        events?.text(#"{"type":"dropin_end","reason":"timeout"}"#)
        for _ in 0..<32 { await Task.yield() }

        XCTAssertEqual(
            rig.model.state,
            .ended(reason: "ended"),
            "a late frame from the socket this call had already closed rewrote its outcome"
        )
    }

    /// A frame sent after the hang-up goes nowhere: the box is empty, so a
    /// capture callback still in flight cannot push audio into a closed call.
    func testACaptureFrameAfterTheHangUpGoesNowhere() async {
        let rig = rig()
        await liveCall(rig)
        rig.model.hangUp()

        rig.audio.feed(Data(repeating: 1, count: DropInAudioFormat.frameBytes))

        XCTAssertTrue(
            rig.transport.link.binary.current.isEmpty,
            "a microphone frame reached the socket after the call was hung up"
        )
    }

    /// A clean close from the peer is `ended`, not `failed`
    /// (`DropinCallClient.kt:135-140`).
    func testAPeerClosingIsAnEndingAndNotAFailure() async {
        let rig = rig()
        await liveCall(rig)

        rig.transport.events.current?.closed()
        await settleUntil({ rig.model.state == .ended(reason: "closed") }, "the close was read")
        XCTAssertNil(PlayerMicArbiter.shared.holder())
    }

    /// A failed upgrade's status reaches the client, so a refused socket can
    /// still raise the pairing screen (`DropinCallClient.kt:128`).
    func testAFailedUpgradeIsReportedAndEndsTheCall() async {
        let rig = rig()
        await rig.model.start(room: "kitchen")
        await settleUntil({ rig.transport.events.current != nil }, "the socket opened")

        rig.transport.events.current?.failed(403)
        await settleUntil(
            { rig.model.state == .failed(note: DropInStrings.connectionLost) },
            "the failure was read"
        )
        XCTAssertNil(PlayerMicArbiter.shared.holder())
        XCTAssertFalse(rig.audio.open)
    }

    // MARK: - the frames and the cutter

    /// A frame with no `type`, and text that is not JSON at all, are both
    /// nothing rather than a crash or a failure.
    func testAFrameWithNoTypeIsNothing() {
        XCTAssertNil(DropInFrame.parse("not json"))
        XCTAssertNil(DropInFrame.parse(#"{"peer_room":"kitchen"}"#))
        XCTAssertNil(DropInFrame.parse("[1,2,3]"))
        XCTAssertEqual(
            DropInFrame.parse(#"{"type":"dropin_end","reason":"silence"}"#),
            DropInFrame(type: "dropin_end", reason: "silence")
        )
    }

    /// A `type` this app does not know leaves the call alone — the server may
    /// grow one, and a call that ended on an unread word is the worse answer.
    func testAnUnknownFrameLeavesTheCallAlone() async {
        let rig = rig()
        await liveCall(rig)

        rig.transport.events.current?.text(#"{"type":"dropin_quality","mos":4.1}"#)
        for _ in 0..<32 { await Task.yield() }

        XCTAssertEqual(rig.model.state, .live(peer: "kitchen"))
    }

    /// **30 ms frames, whatever size the hardware hands over.** A capture
    /// buffer is almost never a whole number of 960-byte frames, and the Pi
    /// relays verbatim — so the cutter holds the remainder rather than
    /// sending a short frame.
    func testTheCutterOnlyEmitsWholeFrames() {
        var cutter = DropInFrameCutter()
        XCTAssertEqual(cutter.take(Data(repeating: 0, count: 683)).count, 0)
        XCTAssertEqual(cutter.pending, 683)

        let second = cutter.take(Data(repeating: 0, count: 683))
        XCTAssertEqual(second.count, 1, "1366 bytes is one whole frame and a remainder")
        XCTAssertEqual(second[0].count, DropInAudioFormat.frameBytes)
        XCTAssertEqual(cutter.pending, 1366 - DropInAudioFormat.frameBytes)

        // Three frames' worth at once, which a 48 kHz buffer of 2048 frames
        // converts to.
        let third = cutter.take(Data(repeating: 0, count: DropInAudioFormat.frameBytes * 3))
        XCTAssertEqual(third.count, 3)
        XCTAssertTrue(third.allSatisfy { $0.count == DropInAudioFormat.frameBytes })
    }

    /// `reset()` drops the remainder, which is what muting does: unmuting
    /// must not send a frame half of which is from before the mute.
    func testResettingTheCutterDropsTheHeldRemainder() {
        var cutter = DropInFrameCutter()
        _ = cutter.take(Data(repeating: 0, count: 500))
        XCTAssertEqual(cutter.pending, 500)
        cutter.reset()
        XCTAssertEqual(cutter.pending, 0)
        XCTAssertEqual(cutter.take(Data(repeating: 0, count: 500)).count, 0)
        XCTAssertEqual(cutter.pending, 500, "the reset remainder came back")
    }

    /// `phone-info`'s shape, read from the route and not from the Kotlin: a
    /// null host decodes as nil, and the two fields nothing builds the URL
    /// from are still read.
    func testPhoneInfoDecodesTheRoutesOwnShape() throws {
        let body = #"""
        {"room_id":"kitchen","domovoi_host":null,"domovoi_port":6370,
         "ws_path":"/v1/dropin/kitchen","audio_sample_rate":16000}
        """#
        let info = try JSONDecoder().decode(DropInPhoneInfo.self, from: Data(body.utf8))
        XCTAssertEqual(info.roomID, "kitchen")
        XCTAssertNil(info.domovoiHost)
        XCTAssertEqual(info.domovoiPort, 6370)
        XCTAssertEqual(info.wsPath, "/v1/dropin/kitchen")
        XCTAssertEqual(info.audioSampleRate, DropInAudioFormat.sampleRate)
    }

    // MARK: - RoomsModel: the hold a pushed room screen needs (D56)

    private func roomsRig() -> (RoomsModel, InMemoryShellStore) {
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({
                URLComponents(url: $0, resolvingAgainstBaseURL: false)
            }) else { return .json("{}") }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            if parts.path == "/api/satellites" {
                return .json(#"[{"room_id":"kitchen","status":"online"}]"#)
            }
            return .json("{}")
        }
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let model = RoomsModel(
            in: container,
            nowMs: { 0 },
            wait: { _ in try await Task.sleep(for: .seconds(3_600)) }
        )
        return (model, store)
    }

    /// **The push must not stop the screen it is pushed on top of.**
    ///
    /// Opening a room fires the roster's `onDisappear` while the roster stays
    /// in the hierarchy, so the roster releases and the room screen holds —
    /// and the model has to stay running, because the pushed screen reads
    /// from it. Without the count this is `stop()`: `byRoom` dropped, the
    /// event sink gone, the roster read stopped under the screen drawing it.
    ///
    /// Both orders, because SwiftUI guarantees neither.
    func testAPushedRoomKeepsTheModelRunningInEitherOrder() async {
        for roomFirst in [true, false] {
            let (model, _) = roomsRig()
            model.hold()                                  // the roster appeared
            XCTAssertTrue(model.onScreen)

            if roomFirst {
                model.hold()                              // the room screen appeared
                model.release()                           // the roster disappeared
            } else {
                model.release()                           // the roster disappeared
                model.hold()                              // the room screen appeared
            }
            for _ in 0..<8 { await Task.yield() }

            XCTAssertTrue(
                model.onScreen,
                "the model stopped while a pushed room was drawing from it "
                    + "(room screen first: \(roomFirst))"
            )
            XCTAssertEqual(model.holderCount, 1)
            StubURLProtocol.reset()
        }
    }

    /// Leaving Rooms altogether, with a room still pushed, **does** stop it:
    /// "a route holds nothing".
    func testLeavingRoomsWithARoomOpenStopsTheModel() async {
        let (model, _) = roomsRig()
        model.hold()
        model.hold()

        model.release()
        model.release()
        await settleUntil({ !model.onScreen }, "the model stopped")

        XCTAssertEqual(model.holderCount, 0)
    }

    /// Popping a room back to the roster does not stop and restart
    /// everything, even in the order where the count passes through zero.
    ///
    /// The stop is deferred one turn of the main actor for this: without it,
    /// a pop in that order drops every per-room read — and ends a live
    /// drop-in call — for the length of an animation.
    func testPoppingARoomDoesNotStopTheModel() async {
        let (model, _) = roomsRig()
        // The push, in full: the roster appears, the room screen appears on
        // top of it, and the roster's `onDisappear` fires behind it.
        model.hold()
        model.hold()
        model.release()
        XCTAssertEqual(model.holderCount, 1)

        // The pop, in the order where the count passes through zero.
        model.release()                                   // the room screen went
        model.hold()                                      // the roster came back
        for _ in 0..<8 { await Task.yield() }

        XCTAssertTrue(model.onScreen, "popping a room stopped the whole screen")
        XCTAssertEqual(model.holderCount, 1)
    }

    /// `+ add` is the server's answer and nothing else (D55): a row the
    /// server did not offer cannot be added, however it is asked for.
    ///
    /// The guard is in ``RoomsModel/addToLibrary(_:_:)`` and this is what
    /// pins it now that the missing-fulfiller refusal path is gone — there is
    /// no sentence left to tell a refused add from an add nobody made.
    func testARowTheServerDidNotOfferIsNeverAdded() async {
        let (model, _) = roomsRig()
        model.hold()
        let room = RoomsSatellite(roomID: "kitchen", status: "online")
        let row = RoomsPlayed(id: 9, source: "library", title: "Marmalade Skies", canAdd: false)

        model.addToLibrary(room, row)
        for _ in 0..<32 { await Task.yield() }

        XCTAssertTrue(
            StubURLProtocol.recorded.filter { $0.url?.path == "/api/music/add-by-url" }.isEmpty,
            "an add went out for a row the server said could not be added"
        )
        XCTAssertNil(model.toast, "something was said about an add that was never made")
    }

    // MARK: - the UI-case seam

    /// The seam is reached only by its own argument, with a value it knows.
    ///
    /// The nil cases are the ones that matter: an ordinary launch, and a
    /// mistyped or truncated argument on a train, must all leave the app its
    /// real microphone rather than half-replacing it.
    func testTheDropInSeamIsOnlyReachedByItsOwnArgument() {
        XCTAssertNil(DropInTestSeam.asked([]), "an empty launch asked for a seam")
        XCTAssertNil(
            DropInTestSeam.asked(["-domovoi-server", "127.0.0.1:6601"]),
            "an ordinary launch asked for a seam"
        )
        XCTAssertNil(
            DropInTestSeam.asked([DropInTestSeam.flag]),
            "the flag with no value asked for a seam"
        )
        XCTAssertNil(
            DropInTestSeam.asked([DropInTestSeam.flag, "loop"]),
            "an unknown value asked for a seam instead of being ignored"
        )
        XCTAssertEqual(
            DropInTestSeam.deny,
            DropInTestSeam.asked(["-domovoi-token", "t", DropInTestSeam.flag, "deny"])
        )
        XCTAssertEqual(
            DropInTestSeam.pump,
            DropInTestSeam.asked([DropInTestSeam.flag, "pump", "-UIPreferredContentSizeCategoryName", "x"])
        )
    }

    /// The pump hands whole frames up, stops while muted, and stops for good.
    ///
    /// Timed, because a timer is the whole of what this class is: 30 ms a
    /// frame, so a tenth of a second is two or three. The assertions are
    /// "more than none" and "no more after the mute", never a count — a
    /// Simulator under load delivers a different number every run and a
    /// pinned one would be a flake waiting for the train.
    func testThePumpHandsUpWholeFramesUntilItIsMutedAndStopped() async throws {
        let pump = PumpedDropInAudio()
        let seen = CapturedFrames()
        try pump.start { frame in seen.add(frame) }
        try await Task.sleep(for: .milliseconds(120))
        let running = seen.take()
        XCTAssertFalse(running.isEmpty, "the pump handed up no frames at all")
        XCTAssertTrue(
            running.allSatisfy { $0.count == DropInAudioFormat.frameBytes },
            "the pump handed up a part frame: \(running.map(\.count))"
        )

        pump.setMuted(true)
        _ = seen.take()
        try await Task.sleep(for: .milliseconds(120))
        XCTAssertTrue(
            seen.take().isEmpty,
            "the pump kept sending while it was muted — the microphone stays "
                + "open and the frames stop"
        )

        pump.setMuted(false)
        try await Task.sleep(for: .milliseconds(120))
        XCTAssertFalse(seen.take().isEmpty, "unmuting did not start the frames again")

        pump.stop()
        _ = seen.take()
        try await Task.sleep(for: .milliseconds(120))
        XCTAssertTrue(seen.take().isEmpty, "the pump kept sending after it was stopped")
    }
}

/// What a capture callback handed up, readable from the test's own thread.
///
/// ``PumpedDropInAudio`` calls its sink on a private queue, so this is the
/// only part of that case that needs a lock.
private final class CapturedFrames: @unchecked Sendable {
    private let lock = NSLock()
    private var held: [Data] = []

    func add(_ frame: Data) { lock.withLock { held.append(frame) } }

    /// What has arrived since the last call, and empties the record.
    func take() -> [Data] {
        lock.withLock { () -> [Data] in
            let was = held
            held = []
            return was
        }
    }
}
