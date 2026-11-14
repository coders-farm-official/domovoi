import Foundation
import XCTest
@testable import DomovoiKit

/// The iPhone room against docs/uml/satellite-fixtures.json, the file the
/// core's tests and the Pi's read (Android's `RoomProtocolTest`): its hello is
/// the fixture's room hello with the iPhone's own board, it declares only
/// abilities the core knows, and it understands every frame the core may
/// send a device that declared what it declares.
final class RoomProtocolTests: XCTestCase {

    private func fixtures() throws -> RoomJSON { try RoomTestKit.json("docs/uml/satellite-fixtures.json") }

    /// The fixture's `ios_room` device once the server unit adds it
    /// (ROOMMODE-MAP §9); until then Android's room, whose hello the iPhone
    /// sends with its own board.
    private func device() throws -> (RoomJSON, isIOS: Bool) {
        let f = try fixtures()
        if let ios = f["devices"]?["ios_room"] { return (ios, true) }
        guard let android = f["devices"]?["android_room"] else {
            XCTFail("satellite-fixtures.json has no room device")
            throw RoomNetworkFailure("no device")
        }
        return (android, false)
    }

    func testTheHelloIsTheFixturesRoomHelloWithTheIPhonesBoard() throws {
        let (d, isIOS) = try device()
        guard case .object(var want)? = d["hello"] else { return XCTFail("no hello in \(d)") }
        if !isIOS { want["board"] = .string("ios_room") }
        let ours = RoomProtocol.hello(
            roomID: want["room_id"]?.stringValue ?? "",
            wakeWord: want["wake_word"]?.stringValue ?? "",
            pairingToken: want["pairing_token"]?.stringValue ?? "",
            approvalCode: want["approval_code"]?.stringValue,
            echoCanceller: true
        )
        XCTAssertEqual(RoomJSON.object(want), RoomJSON.object(ours))
        XCTAssertEqual("ios_room", RoomProtocol.board)
        if isIOS { XCTAssertEqual("ios_room", d["expect"]?["board"]?.stringValue) }
    }

    func testWithoutAnEchoCancellerThereIsNoFullDuplexAndNeverChatMode() throws {
        XCTAssertFalse(RoomProtocol.supports(echoCanceller: false).contains("full_duplex"))
        XCTAssertFalse(RoomProtocol.supports(echoCanceller: true).contains("chat_mode"))
        guard case .object(let abilities)? = try fixtures()["abilities"] else { return XCTFail("no abilities") }
        for name in RoomProtocol.supports(echoCanceller: true) {
            XCTAssertNotNil(abilities[name], "\(name) is not an ability the core knows")
        }
    }

    func testEveryFrameTheCoreMaySendIsHandled() throws {
        let f = try fixtures()
        let (d, _) = try device()
        func strings(_ v: RoomJSON?) -> [String] {
            guard case .array(let items)? = v else { return [] }
            return items.compactMap(\.stringValue)
        }
        let frames = strings(f["always_sent"]) + strings(d["expect"]?["frames_sent"])
        XCTAssertFalse(frames.isEmpty)
        for frame in frames {
            // "announcement" is a response_start out of turn, not a frame type.
            let type = frame == "announcement" ? "response_start" : frame
            if case .other = RoomProtocol.parse(#"{"type":"\#(type)"}"#) {
                XCTFail("\(type) is sent to this device and not handled")
            }
        }
        for frame in strings(d["expect"]?["frames_never"]) {
            guard case .other = RoomProtocol.parse(#"{"type":"\#(frame)"}"#) else {
                return XCTFail("\(frame) is never sent here and should be ignored")
            }
        }
    }

    func testTheReadyFeaturesGateTheNewMessageTypes() throws {
        let (d, _) = try device()
        guard let ready = d["ready"], case let .ready(features, _, _, _) = RoomProtocol.parse(ready.serialized) else {
            return XCTFail("the fixture's ready did not parse")
        }
        XCTAssertEqual(["speech_pause", "end_capture", "music_failed", "health", "log_push"], features)
        let pause: RoomProtocol.Frame = ["type": .string("speech_pause")]
        XCTAssertTrue(RoomProtocol.maySend(pause, features: features))
        XCTAssertFalse(RoomProtocol.maySend(pause, features: []))
        XCTAssertFalse(RoomProtocol.maySend(["type": .string("health")], features: ["speech_pause"]))
        XCTAssertTrue(RoomProtocol.maySend(["type": .string("utterance_end")], features: []))
    }

    func testServerFramesParse() {
        XCTAssertEqual(.error(reason: "awaiting_approval", message: "waiting for approval on the dashboard", code: "305117"),
                       RoomProtocol.parse(#"{"type":"error","reason":"awaiting_approval","message":"waiting for approval on the dashboard","code":"305117"}"#))
        XCTAssertEqual(.responseStart(text: "Hi", sampleRate: 22_050), RoomProtocol.parse(#"{"type":"response_start","text":"Hi","audio_sample_rate":22050}"#))
        XCTAssertEqual(.responseEnd(interrupted: false, expectFollowup: true, ignored: false),
                       RoomProtocol.parse(#"{"type":"response_end","interrupted":false,"expect_followup":true}"#))
        XCTAssertEqual(.endCapture(utt: 7), RoomProtocol.parse(#"{"type":"end_capture","utt":7}"#))
        XCTAssertEqual(.setVolume(level: 100), RoomProtocol.parse(#"{"type":"set_volume","level":140}"#))
        XCTAssertEqual(.setWakeWord(slug: "hey_house", threshold: 0.6), RoomProtocol.parse(#"{"type":"set_wake_word","slug":"hey_house","threshold":0.6}"#))
        XCTAssertEqual(.dropinStart(peerRoom: "office", peerLabel: "Office", sampleRate: 16_000, fullDuplex: true, awaitReady: false),
                       RoomProtocol.parse(#"{"type":"dropin_start","peer_room":"office","peer_label":"Office","audio_sample_rate":16000,"full_duplex":true}"#))
        XCTAssertEqual(.other(type: nil), RoomProtocol.parse("not json"))
        XCTAssertEqual(.other(type: "chat_start"), RoomProtocol.parse(#"{"type":"chat_start"}"#))
    }

    func testTheRoomsVoiceIsTheServersToChoose() throws {
        XCTAssertFalse(RoomProtocol.supports(echoCanceller: true).contains("voice"))
        let (d, _) = try device()
        guard let ready = d["ready"], case let .ready(_, _, _, voice) = RoomProtocol.parse(ready.serialized) else {
            return XCTFail("no ready")
        }
        XCTAssertNil(voice)
        XCTAssertEqual(.ready(features: [], roomID: nil, botName: nil, roomVoice: "Ryan"),
                       RoomProtocol.parse(#"{"type":"ready","features":[],"room_voice":"Ryan"}"#))
        XCTAssertEqual(.ready(features: [], roomID: nil, botName: nil, roomVoice: nil), RoomProtocol.parse(#"{"type":"ready","room_voice":"  "}"#))
        XCTAssertEqual(.soundsChanged(roomVoice: "Ryan", saysVoice: true), RoomProtocol.parse(#"{"type":"sounds_changed","room_voice":"Ryan"}"#))
        XCTAssertEqual(.soundsChanged(roomVoice: nil, saysVoice: true), RoomProtocol.parse(#"{"type":"sounds_changed","room_voice":null}"#))
        XCTAssertEqual(.soundsChanged(roomVoice: nil, saysVoice: false), RoomProtocol.parse(#"{"type":"sounds_changed"}"#))
    }

    func testOnlyAnHttpURLIsAStream() {
        XCTAssertTrue(RoomProtocol.isStreamURL("http://192.168.1.30:8051/stream.mp3"))
        XCTAssertFalse(RoomProtocol.isStreamURL("-w /sdcard/x"))
        XCTAssertFalse(RoomProtocol.isStreamURL("file:///etc/passwd"))
        XCTAssertFalse(RoomProtocol.isStreamURL("http://h/a b"))
        XCTAssertFalse(RoomProtocol.isStreamURL(nil))
    }

    func testTheStreamURLFollowsTheCoreURL() {
        XCTAssertEqual("ws://192.168.1.30:6370/v1/stream/kitchen", RoomProtocol.streamURL(coreURL: "http://192.168.1.30:6370", roomID: "kitchen"))
        XCTAssertEqual("wss://h:6370/v1/stream/a", RoomProtocol.streamURL(coreURL: "https://h:6370/", roomID: "a"))
    }

    func testTheHealthSampleCarriesOnlyWhatTheCoreKeepsPlusTheBatteryPair() throws {
        guard case .object(let devices)? = try fixtures()["devices"] else { return XCTFail("no devices") }
        var stored: Set<String> = []
        for d in devices.values {
            if case .object(let keys)? = d["health"]?["stored"] { stored.formUnion(keys.keys) }
        }
        let sample = RoomHealthSample.build(RoomHealthSample.Facts(
            seq: 3, ts: 1_760_280_120.0, rssKB: 98_000, cpuPct: 6.5, tempC: 34.0, uptimeS: 432_000,
            procUptimeS: 5_400, coreOK: true, micExpected: true, micFPS: 0.0, idle: true, wsUp: true,
            wsDownForS: 0, reconnects: 2, wifiRxMbits: 144.0, wifiSSID: nil, wifiSignalDBM: -58,
            batteryPercent: 64, batteryCharging: false, onPower: true, micBlocked: true,
            wakeLoopAgeS: 0.1, threads: 31
        ))
        XCTAssertEqual(.string("health"), sample["type"])
        XCTAssertEqual([], Set(sample.keys).subtracting(stored).subtracting(["type"]))
        XCTAssertNil(sample["battery_percent"])
        let (d, _) = try device()
        XCTAssertEqual(d["health"]?["frame"]?["battery"], sample["battery"])
        XCTAssertEqual(.bool(true), sample["mic_blocked"])
        XCTAssertLessThan(RoomProtocol.text(sample).utf8.count, 4_096)
    }

    func testAnIPhonesFrameLeavesOutWhatIOSWillNotSay() {
        let f = RoomHealthSample.Facts(
            seq: 1, ts: 1.5, rssKB: nil, cpuPct: nil, tempC: nil, uptimeS: 1, procUptimeS: 1, coreOK: true,
            micExpected: true, micFPS: 33.3, idle: true, wsUp: true, wsDownForS: 0, reconnects: 0,
            wifiRxMbits: nil, wifiSSID: nil, wifiSignalDBM: nil, batteryPercent: 80, batteryCharging: true,
            onPower: true, micBlocked: false, wakeLoopAgeS: nil, threads: nil
        )
        let sample = RoomHealthSample.build(f)
        XCTAssertNil(sample["wifi"])
        XCTAssertNil(sample["soc_temp_c"])
        XCTAssertEqual(.object(["percent": .int(80), "charging": .bool(true), "on_power": .bool(true)]), sample["battery"])
    }

    // -- 220085d8: drop-in after the reply, the wake gate's report --

    func testDropinReadyIsDeclaredOnlyWithTheEchoCanceller() throws {
        XCTAssertTrue(RoomProtocol.supports(echoCanceller: true).contains("dropin_ready"))
        XCTAssertFalse(RoomProtocol.supports(echoCanceller: false).contains("dropin_ready"))
        let hello = RoomProtocol.hello(roomID: "nursery", wakeWord: "hey_jarvis", pairingToken: "t", approvalCode: "305117", echoCanceller: false)
        guard case .array(let supports)? = hello["supports"] else { return XCTFail("no supports") }
        XCTAssertFalse(supports.contains(.string("dropin_ready")))
        XCTAssertEqual("dropin_ready", try fixtures()["abilities"]?["dropin_ready"]?["hello_key"]?.stringValue)
    }

    func testTheDropinStartsAwaitReadyAndTheReadyFrameAreTheContracts() {
        XCTAssertEqual(.dropinStart(peerRoom: "office", peerLabel: "office", sampleRate: 16_000, fullDuplex: true, awaitReady: true),
                       RoomProtocol.parse(#"{"type":"dropin_start","peer_room":"office","peer_label":"office","audio_sample_rate":16000,"full_duplex":true,"await_ready":true}"#))
        XCTAssertEqual(.dropinStart(peerRoom: "office", peerLabel: nil, sampleRate: 16_000, fullDuplex: true, awaitReady: false),
                       RoomProtocol.parse(#"{"type":"dropin_start","peer_room":"office","audio_sample_rate":16000,"full_duplex":true}"#))
        XCTAssertEqual(#"{"type":"dropin_ready"}"#, RoomProtocol.text(RoomProtocol.dropinReady))
        XCTAssertEqual(.responseEnd(interrupted: false, expectFollowup: false, ignored: true),
                       RoomProtocol.parse(#"{"type":"response_end","interrupted":false,"expect_followup":false,"ignored":true}"#))
        XCTAssertEqual(.responseEnd(interrupted: false, expectFollowup: false, ignored: false), RoomProtocol.parse(#"{"type":"response_end"}"#))
    }

    func testTheReportedConfigCarriesTheThreeWakeKeys() {
        let f = RoomProtocol.configStatus(threshold: 0.6, patienceFrames: WakeGate.patienceDefault, cooldownSec: WakeGate.cooldownSecDefault)
        XCTAssertEqual(.string("config_status"), f["type"])
        guard case .object(let config)? = f["config"] else { return XCTFail("no config") }
        XCTAssertEqual(["wake.threshold", "wake.patience_frames", "wake.cooldown_sec"], Set(config.keys))
        // 0.6 as a Float is reported as 0.6, the value the Settings tab shows.
        XCTAssertEqual(#"{"config":{"wake.cooldown_sec":2.0,"wake.patience_frames":1,"wake.threshold":0.6},"type":"config_status"}"#,
                       RoomProtocol.text(f))
        XCTAssertTrue(RoomProtocol.maySend(f, features: []))
    }

    func testAPushedThresholdIsTheRoomsOneThresholdAndAMissingOneKeepsIt() {
        guard case let .setWakeWord(_, pushed) = RoomProtocol.parse(#"{"type":"set_wake_word","slug":"hey_house","threshold":0.62}"#) else {
            return XCTFail("no set_wake_word")
        }
        XCTAssertEqual(0.62, RoomWakeSettings.thresholdValue(pushed))
        guard case let .setWakeWord(_, bare) = RoomProtocol.parse(#"{"type":"set_wake_word","slug":"hey_domovoi"}"#) else {
            return XCTFail("no set_wake_word")
        }
        XCTAssertNil(RoomWakeSettings.thresholdValue(bare))
        XCTAssertNil(RoomWakeSettings.thresholdValue(1.5))
        XCTAssertNil(RoomWakeSettings.thresholdValue(-0.1))
        XCTAssertNil(RoomWakeSettings.thresholdValue(.nan))
        let t = RoomWakeSettings.thresholdValue(pushed) ?? 0
        XCTAssertEqual(.double(0.62), RoomProtocol.configStatus(threshold: t, patienceFrames: 1, cooldownSec: 2.0)["config"]?["wake.threshold"])
        XCTAssertEqual(t, WakeGate(threshold: t).threshold)
    }

    func testTheTurnCarriesTheWakesPeakScore() {
        let f = RoomProtocol.utteranceStart(trigger: "wake_word", utt: 3, backlogMS: 0, wakeMS: 412, wakeScore: 0.91234)
        XCTAssertEqual(.double(0.912), f["wake_score"])
        XCTAssertTrue(RoomProtocol.text(f).contains(#""wake_score":0.912"#), RoomProtocol.text(f))
        XCTAssertEqual(.int(412), f["wake_ms"])
        let g = WakeGate(threshold: 0.5, patience: 2, cooldownSec: 0)
        _ = g.feed(0.7, now: 0)
        XCTAssertTrue(g.feed(0.88, now: 80))
        XCTAssertEqual(.double(0.88), RoomProtocol.utteranceStart(trigger: "wake_word", utt: 4, backlogMS: 0, wakeMS: 10, wakeScore: g.lastPeak)["wake_score"])
        XCTAssertNil(RoomProtocol.utteranceStart(trigger: "followup", utt: 5, backlogMS: 0, wakeMS: nil, wakeScore: 0.9)["wake_score"])
        XCTAssertNil(RoomProtocol.utteranceStart(trigger: "wake_word", utt: 6, backlogMS: 0, wakeMS: 10, wakeScore: nil)["wake_score"])
    }

    func testTheReaderKeepsWholeNumbersApartFromFractions() {
        XCTAssertEqual(.object(["a": .int(2), "b": .double(2.0), "c": .double(0.5), "d": .string("é😀\n")]),
                       RoomJSON.parse(#"{"a":2,"b":2.0,"c":5e-1,"d":"é😀\n"}"#))
        XCTAssertNil(RoomJSON.parse(#"{"a":1"#))
        XCTAssertNil(RoomJSON.parse(#"{"a":1} x"#))
        XCTAssertNil(RoomJSON.parse("[01x]"))
    }
}
