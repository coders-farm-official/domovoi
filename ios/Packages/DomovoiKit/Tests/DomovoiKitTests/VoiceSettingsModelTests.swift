import Foundation
import XCTest
@testable import DomovoiKit

/// The Voices section's model against a server that is not a server: what it
/// asks for, which voice it will offer to play, and what it does with an
/// answer that arrived for a question nobody is asking any more.
///
/// `PhoneVoice` has no Kotlin test either — Android's JVM suite covers the
/// phonemizer and not the card — so the read-after-await guard it already has
/// at `PhoneVoice.kt:264` is specified by nothing over there. It is specified
/// here.
@MainActor
final class VoiceSettingsModelTests: XCTestCase {

    private let serverA = "http://10.0.0.42:6369"
    private let serverB = "http://10.0.0.77:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    /// A sink that makes no noise and remembers everything.
    ///
    /// The bytes are kept, not just a count: every assertion below about
    /// "it played the voice it named" compares the **body** the stub served
    /// for that id, so a button wired to the wrong row cannot pass by
    /// playing something.
    @MainActor
    private final class Ears: VoiceSettingsSampleSink {
        var played: [Data] = []
        var stops = 0
        /// Armed to make `play` throw, for the bytes-that-will-not-decode
        /// path.
        var refuse: (any Error)?
        private var finish: (@MainActor () -> Void)?

        func play(_ data: Data, onFinish: @escaping @MainActor () -> Void) throws {
            if let refuse { throw refuse }
            played.append(data)
            finish = onFinish
        }

        func stop() {
            stops += 1
            finish = nil
        }

        /// The clip reached its end on its own.
        func finished() {
            let done = finish
            finish = nil
            done?()
        }

        /// The bodies as text, for a readable assertion.
        var playedText: [String] {
            played.map { String(data: $0, encoding: .utf8) ?? "<\($0.count) bytes>" }
        }
    }

    private static let norman = "en_US-norman-medium"
    private static let kristin = "en_US-kristin-medium"
    private static let ljspeech = "en_US-ljspeech-medium"

    /// The house's `/api/voice*`. One voice of each sample source, exactly as
    /// the server's own map can be: `"voice"`, `"clip"`, `null` — so "a
    /// button per voice" and "a button where the server says yes" are
    /// different answers.
    private final class House: Sendable {
        /// `server_voice`, which may be an id outside the three.
        let serverVoice = Shared<String>(VoiceSettingsModelTests.kristin)
        /// Which voices the server says it can really speak.
        let sources = Shared<[String: String?]>([
            VoiceSettingsModelTests.norman: "voice",
            VoiceSettingsModelTests.kristin: "clip",
            VoiceSettingsModelTests.ljspeech: nil,
        ])
        let infoStatus = Shared<Int>(200)
        let samplesStatus = Shared<Int>(200)
        let sampleStatus = Shared<Int>(200)

        /// The sample body **names its own voice**, so an assertion that the
        /// right sample played is an assertion about which id was asked for
        /// and not merely that bytes arrived.
        static func wav(_ id: String) -> Data { Data("WAV:\(id)".utf8) }

        private func infoBody() -> String {
            """
            {"voices": [
               {"id": "\(VoiceSettingsModelTests.norman)", "name": "Norman", "sex": "male",
                "sample_rate": 22050, "size_bytes": 63531379, "sha256": "b97394aa"},
               {"id": "\(VoiceSettingsModelTests.kristin)", "name": "Kristin", "sex": "female",
                "sample_rate": 22050, "size_bytes": 63531379, "sha256": "c21180ff"},
               {"id": "\(VoiceSettingsModelTests.ljspeech)", "name": "Linda", "sex": "female",
                "sample_rate": 22050, "size_bytes": 63531379, "sha256": "d44021ab"}],
             "server_voice": "\(serverVoice.current)",
             "pronunciations": [{"word": "Caoimhe", "say_like": "kee-va"}],
             "updated_at": "2026-11-01T18:04:11Z"}
            """
        }

        private func samplesBody() -> String {
            let pairs = sources.current.map { id, source in
                "\"\(id)\": \(source.map { "\"\($0)\"" } ?? "null")"
            }.sorted().joined(separator: ", ")
            return "{\"samples\": {\(pairs)}}"
        }

        /// A pure answer, so it can also serve as `parkRequest`'s
        /// `otherwise`.
        func answer(_ request: URLRequest) -> StubURLProtocol.Answer {
            guard
                let url = request.url,
                let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
            else { return .json("{}", status: 404) }
            switch parts.path {
            case "/api/health":
                // The identity gate's probe. A server that did not answer it
                // would refuse every read below, and every assertion over
                // them would be an assertion about nothing.
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            case "/api/voice":
                guard infoStatus.current == 200 else {
                    return .json(#"{"detail":"the voice settings are away"}"#, status: infoStatus.current)
                }
                return .json(infoBody())
            case "/api/voice/samples":
                guard samplesStatus.current == 200 else {
                    return .json(#"{"detail":"no sample sources"}"#, status: samplesStatus.current)
                }
                return .json(samplesBody())
            case let path where path.hasPrefix("/api/voice/sample/"):
                let id = String(path.dropFirst("/api/voice/sample/".count))
                guard sampleStatus.current == 200 else {
                    return .json(#"{"detail":"the sample could not be made"}"#, status: sampleStatus.current)
                }
                return StubURLProtocol.Answer(
                    headers: ["Content-Type": "audio/wav", "X-Sample-Source": "voice"],
                    body: Self.wav(id)
                )
            default:
                return .json("{}", status: 404)
            }
        }

        func install() {
            StubURLProtocol.dispatch { [self] request in answer(request) }
        }
    }

    /// Both servers are trusted and pinned to the same identity, so a switch
    /// between them is a switch the app can really make — an untrusted
    /// address cannot be written (``TrustBook/setServerURL(_:)``) and a rig
    /// that forced one would be testing a state the app cannot reach.
    private func rig(
        house: House = House()
    ) -> (VoiceSettingsModel, House, Ears, InMemoryShellStore, AppContainer) {
        let store = InMemoryShellStore(serverURL: serverA, token: "household-abc")
        store.trustServer(serverA, identity: TestIdentity.real.pin)
        store.trustServer(serverB, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let ears = Ears()
        return (VoiceSettingsModel(in: container, sink: ears), house, ears, store, container)
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

    /// Both reads landed, which is what every assertion about the section's
    /// contents rests on.
    private func loaded(_ model: VoiceSettingsModel) async {
        model.refresh()
        await settleUntil({ model.info.data != nil && model.samples.data != nil }, "the two reads")
    }

    // MARK: - the reads

    func testTheSectionReadsTheVoicesAndTheSampleSources() async {
        let (model, house, _, _, _) = rig()
        house.install()
        await loaded(model)
        let info = model.info.data
        XCTAssertEqual(info?.voices.map(\.name), ["Norman", "Kristin", "Linda"])
        XCTAssertEqual(VoiceSettingsNaming.houseVoiceLabel(info!), "Kristin (female)")
        // The seeded word, which appears nowhere in the built-in wording.
        XCTAssertEqual(info?.entries.map(\.word), ["Caoimhe"])
        XCTAssertEqual(model.samples.data?.source(of: Self.norman), .voice)
    }

    /// The samples read failing must not blank the voice list: the list is
    /// the honest part, and the map only decides whether a button is drawn.
    func testTheSamplesReadFailingLeavesTheVoiceListStanding() async {
        let (model, house, _, _, _) = rig()
        house.samplesStatus.set(500)
        house.install()
        model.refresh()
        await settleUntil({ model.info.data != nil && model.samples.error != nil }, "the two reads")
        XCTAssertEqual(model.info.data?.voices.count, 3)
        XCTAssertNil(model.samples.data)
        // And no button anywhere, which is the safe side of the rule.
        for id in [Self.norman, Self.kristin, Self.ljspeech] {
            XCTAssertFalse(model.canPlay(id), "\(id) should have no button")
        }
    }

    /// The info read failing is **said**, not drawn as an empty card. The
    /// section's own view reads `answered` and `error`; this is the state it
    /// reads.
    func testTheInfoReadFailingIsAnErrorAndNotAnEmptySection() async {
        let (model, house, _, _, _) = rig()
        house.infoStatus.set(500)
        house.install()
        model.refresh()
        await settleUntil({ model.info.answered }, "the info read")
        XCTAssertNil(model.info.data)
        XCTAssertNotNil(model.info.error)
        XCTAssertEqual(model.info.error?.contains("the voice settings are away"), true)
    }

    // MARK: - the one control

    /// Three voices, one button — on the one the server says it can speak.
    /// A `"clip"` voice is Ogg Opus, which AVFoundation does not decode, and
    /// a `null` one is the server saying there is nothing that is really that
    /// voice.
    func testOnlyTheVoiceTheServerCanSpeakGetsAButton() async {
        let (model, house, _, _, _) = rig()
        house.install()
        await loaded(model)
        XCTAssertTrue(model.canPlay(Self.norman))
        XCTAssertFalse(model.canPlay(Self.kristin))
        XCTAssertFalse(model.canPlay(Self.ljspeech))
        XCTAssertEqual(model.whyNoSample(Self.norman), nil)
        XCTAssertEqual(model.whyNoSample(Self.kristin), VoiceSettingsStrings.noSampleToPlay)
    }

    /// The sample route 404s for anything outside `PIPER_PINS`
    /// (`voice.py:171`), so a button for a voice the section is not drawing
    /// could only fail. The samples map is allowed to mention one; the model
    /// is not allowed to offer it.
    func testAVoiceTheInfoReadDidNotListGetsNoButtonEvenIfTheSamplesMapSaysYes() async {
        let (model, house, _, _, _) = rig()
        house.sources.mutate { $0["en_GB-cori-high"] = "voice" }
        house.install()
        await loaded(model)
        XCTAssertEqual(model.samples.data?.source(of: "en_GB-cori-high"), .voice)
        XCTAssertFalse(model.canPlay("en_GB-cori-high"))
    }

    /// Before the samples read answers there is no predicate, so there is no
    /// button and nothing to say about why.
    func testNothingIsOfferedBeforeTheSamplesReadAnswers() {
        let (model, house, _, _, _) = rig()
        house.install()
        XCTAssertFalse(model.canPlay(Self.norman))
        XCTAssertNil(model.whyNoSample(Self.norman))
    }

    /// A tap that could not work must not become a request. A guard the view
    /// also has is still a guard the model needs: the view is not the only
    /// caller, and the route would 404.
    func testTappingAVoiceWithNoButtonAsksForNothing() async {
        let (model, house, ears, _, _) = rig()
        house.install()
        await loaded(model)
        model.tappedSample(Self.kristin)
        for _ in 0..<200 { await Task.yield() }
        XCTAssertEqual(model.sampleFetches, 0)
        XCTAssertEqual(ears.played.count, 0)
        XCTAssertNil(model.playingVoiceID)
        XCTAssertNil(model.sampleError)
    }

    // MARK: - the state machine

    func testASampleIsAskedForByModelRefAndPlaysTheVoiceItNames() async {
        let (model, house, ears, _, _) = rig()
        house.install()
        await loaded(model)
        model.tappedSample(Self.norman)
        await settleUntil({ model.playingVoiceID != nil }, "the sample")
        XCTAssertEqual(model.lastSamplePath, "/api/voice/sample/\(Self.norman)")
        XCTAssertEqual(model.sampleFetches, 1)
        XCTAssertEqual(model.playingVoiceID, Self.norman)
        XCTAssertNil(model.fetchingSampleID)
        XCTAssertNil(model.sampleError)
        // The bytes name their own voice: this is the assertion a button
        // wired to the wrong row fails.
        XCTAssertEqual(ears.playedText, ["WAV:\(Self.norman)"])
    }

    /// One button, two labels (`VoicePanel.kt:210`): a tap on the row that is
    /// playing stops it.
    func testASecondTapOnThePlayingRowStopsIt() async {
        let (model, house, ears, _, _) = rig()
        house.install()
        await loaded(model)
        model.tappedSample(Self.norman)
        await settleUntil({ model.playingVoiceID != nil }, "the sample")
        model.tappedSample(Self.norman)
        XCTAssertNil(model.playingVoiceID)
        XCTAssertGreaterThan(ears.stops, 0)
        XCTAssertEqual(model.sampleFetches, 1, "stopping must not ask for it again")
    }

    /// The clip ran to its end: the button goes back to saying
    /// "play a sample" without the view watching anything.
    func testReachingTheEndClearsThePlayingRow() async {
        let (model, house, ears, _, _) = rig()
        house.install()
        await loaded(model)
        model.tappedSample(Self.norman)
        await settleUntil({ model.playingVoiceID != nil }, "the sample")
        ears.finished()
        XCTAssertNil(model.playingVoiceID)
    }

    /// The server's own wording, said. Not silence: a sample that plays
    /// nothing and says nothing is the failure `PhoneVoice.kt:371` records.
    func testASampleTheServerCannotMakeIsSaid() async {
        let (model, house, ears, _, _) = rig()
        house.install()
        await loaded(model)
        house.sampleStatus.set(409)
        model.tappedSample(Self.norman)
        await settleUntil({ model.sampleError != nil }, "the failure")
        XCTAssertNil(model.playingVoiceID)
        XCTAssertNil(model.fetchingSampleID)
        XCTAssertEqual(ears.played.count, 0)
        XCTAssertEqual(model.sampleError?.contains("the sample could not be made"), true)
    }

    /// Bytes that will not decode are said too, for the same reason.
    func testBytesThatWillNotDecodeAreSaidRatherThanPlayedSilently() async {
        let (model, house, ears, _, _) = rig()
        house.install()
        await loaded(model)
        ears.refuse = DomovoiError.notPermitted("that is not audio this phone plays")
        model.tappedSample(Self.norman)
        await settleUntil({ model.sampleError != nil }, "the failure")
        XCTAssertNil(model.playingVoiceID)
        XCTAssertEqual(
            model.sampleError,
            VoiceSettingsStrings.sampleFailed("that is not audio this phone plays")
        )
    }

    // MARK: - read after await

    /// **The top defect class on this track.** The server is switched
    /// *inside* the sample's round trip; house A's bytes must be dropped
    /// rather than played under house B's voice list.
    ///
    /// The switch happens while the request is parked at the stub, which is
    /// the only honest way to test this: a switch before the tap, or after
    /// the answer, passes against a model with no guard at all. The park is
    /// bounded, and `ranOut` is asserted — a park that outlasted the
    /// session's own timeout would turn this into a test that cannot fail.
    func testHouseAsSampleIsDroppedWhenThePhoneMovesToHouseBMidFlight() async {
        let (model, house, ears, store, _) = rig()
        house.install()
        await loaded(model)

        let park = parkRequest(
            to: "/api/voice/sample/\(Self.norman)",
            answering: { StubURLProtocol.Answer(body: House.wav(VoiceSettingsModelTests.norman)) },
            otherwise: { house.answer($0) }
        )
        model.tappedSample(Self.norman)
        await settle(for: park.arrived, what: "the sample request")
        XCTAssertEqual(model.fetchingSampleID, Self.norman)

        // Inside the round trip.
        XCTAssertTrue(store.setServerURL(serverB))
        park.release.set(true)

        await settleUntil({ model.fetchingSampleID == nil }, "the dropped sample")
        XCTAssertFalse(park.ranOut.current, "the park outlasted the request: this test proved nothing")
        XCTAssertNil(model.playingVoiceID)
        XCTAssertEqual(ears.played.count, 0, "house A's bytes were played after the phone left house A")
        // And in silence: a failure line about a house the person has left
        // would be a second wrong answer.
        XCTAssertNil(model.sampleError)
    }

    /// The other half of the same rule: the **row** can move too. A tap on
    /// Norman, a tap on Linda while Norman is still in flight, and then
    /// Norman's bytes arriving must not play under Linda's button.
    func testTheFirstTapsBytesDoNotPlayUnderTheSecondTapsRow() async {
        let house = House()
        // Two playable voices, so there are two buttons to tap.
        house.sources.mutate { $0[VoiceSettingsModelTests.ljspeech] = "voice" }
        let (model, _, ears, _, _) = rig(house: house)
        house.install()
        await loaded(model)

        let park = parkRequest(
            to: "/api/voice/sample/\(Self.norman)",
            answering: { StubURLProtocol.Answer(body: House.wav(VoiceSettingsModelTests.norman)) },
            otherwise: { house.answer($0) }
        )
        model.tappedSample(Self.norman)
        await settle(for: park.arrived, what: "Norman's sample request")

        // The second tap, while the first is parked. It cancels the first
        // task, so Norman's answer may never be delivered at all — and if it
        // is, ``claim`` files it as superseded. Either way Linda plays and
        // Norman does not.
        model.tappedSample(Self.ljspeech)
        park.release.set(true)
        await settleUntil({ model.playingVoiceID != nil }, "Linda's sample")

        XCTAssertFalse(park.ranOut.current, "the park outlasted the request: this test proved nothing")
        XCTAssertEqual(model.playingVoiceID, Self.ljspeech)
        XCTAssertEqual(ears.playedText, ["WAV:\(Self.ljspeech)"])
        XCTAssertFalse(
            ears.playedText.contains("WAV:\(Self.norman)"),
            "Norman's sample played under Linda's button"
        )
    }

    /// A switch of house silences a sample that is already playing. The two
    /// reads re-key themselves; the noise has to be told.
    func testMovingHouseSilencesAPlayingSample() async {
        let (model, house, ears, store, container) = rig()
        house.install()
        await loaded(model)
        model.tappedSample(Self.norman)
        await settleUntil({ model.playingVoiceID != nil }, "the sample")
        XCTAssertTrue(store.setServerURL(serverB))
        // What the shell does after a switch (``ShellModel/refresh()``), and
        // the only thing that fires ``AppContainer/onServerChange(_:)``. A
        // store write alone re-keys nothing — which is itself worth asserting
        // once, so a future reader does not take the silence for granted.
        XCTAssertNotNil(model.playingVoiceID, "a store write alone is not the server key")
        container.serverAddressChanged()
        await settleUntil({ model.playingVoiceID == nil }, "the silence")
        XCTAssertGreaterThan(ears.stops, 0)
    }
}
