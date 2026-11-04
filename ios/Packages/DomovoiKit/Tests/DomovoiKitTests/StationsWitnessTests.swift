import Foundation
import XCTest
@testable import DomovoiKit

/// Which audio engine a tapped station reaches — the whole of
/// ``StationsModel/playback``'s mutability, in the one file that is about it.
///
/// **This file exists because review 3 of the 2026-11-03 batch sabotaged a
/// rule nothing measured.** The fix for review 2's S2 turned
/// ``StationsModel/playback`` from a `let` into a `var` that ``StationsView``
/// re-points on **every appearance** (`StationsView.swift:121`, `:142`), which
/// is what the other four screens of this unit already did. That made the
/// capture at `StationsModel.swift:780` load-bearing: before S2 it was
/// redundancy over an immutable property, and after it the witness can change
/// while a `POST /play` is open. The verifier deleted that line, and
/// `StationsModelTests` stayed 31 of 31 green — the two house-move cases
/// there both go through `serverChanged()`, so the epoch guard absorbs them
/// and neither switches the **witness** inside the round trip.
///
/// So the two rules, in the two shapes that discriminate:
///
/// 1. **A witness handed in after the model was built is the one the next tap
///    reaches** — moved here from `StationsModelTests` (a file this unit does
///    not own; review 3's second must). It is `use(_:)`'s own case and keeps
///    its name, so review 2's sabotage table still resolves.
/// 2. **The witness the tap was made against is the one that is played
///    through**, even when the view re-points the model while the POST is in
///    flight. The switch happens **inside** the round trip, which
///    [[read-after-await-wrong-key]] — the top defect class on this track —
///    says is the only arrangement that fails against the broken code. It
///    does: with `let playback = self.playback` deleted, the station lands on
///    the witness that arrived mid-flight.
///
/// Not a live bug in today's app, and the case says so rather than claiming
/// one: both witnesses are a ``StationsPhonePlayback`` struct over the same
/// ``ShellModel/player``, so there is no screen today on which the two
/// answers differ. It is the rule the `var` depends on — a tap is a tap on
/// the engine the gate was checked against — and the next engine (a cast
/// target, a second player) is what makes the difference visible. A rule a
/// comment asserts and no case measures is this track's named class of
/// defect, which is exactly how it was found.
@MainActor
final class StationsWitnessTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    /// The id `POST /play` answers — the **saved** row, which is how an
    /// unsaved directory hit gets one, and which no other fixture in this
    /// file uses.
    private let savedID = 101

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the house

    private func row(id: Int, name: String) -> String {
        """
        {"id":\(id),"name":"\(name)","source":"online",
         "stream_url":"https://stream.example/\(id)","external_id":"rb-\(id)",
         "country_code":"US","language":"english","tags":["indie"],
         "favorited":true,"sample_interval_sec":180}
        """
    }

    /// Two favourites, no Recent, and a `/play` that is **held open** until
    /// the case releases it.
    ///
    /// Built on ``parkRequest(to:answering:otherwise:)`` — `Shared.swift`'s
    /// one copy of "a request held at the server", which exists for this
    /// exact shape and is bounded under the session's own 1 s timeout, so a
    /// case that forgets to release fails rather than leaving an orphaned
    /// `xctest` on the shared Mac.
    private func house() -> ParkedRequest {
        let played = row(id: savedID, name: "Catalogue Ibis 01")
        let favorites = "[" + [row(id: 1, name: "Favourite Kestrel 01"),
                               row(id: 2, name: "Favourite Kestrel 02")].joined(separator: ",") + "]"
        return parkRequest(
            to: StationsPath.play,
            answering: { .json(played) },
            otherwise: { request in
                guard
                    let url = request.url,
                    let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                let query = parts.queryItems ?? []
                switch parts.path {
                case "/api/health":
                    // The identity gate's probe. Without it every read is
                    // refused and every assertion below is about nothing.
                    let challenge = query.first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                case "/api/plugins/radio/stations":
                    return .json(favorites)
                case "/api/plugins/radio/recent":
                    return .json("[]")
                case "/api/plugins/radio/badge":
                    return .json(#"{"favorites":2}"#)
                case "/api/plugins/radio/search":
                    return .json("[]")
                default:
                    return .json("{}", status: 404)
                }
            }
        )
    }

    private func rig(playback: StationsPlaybackSpy) -> (StationsModel, ParkedRequest) {
        let park = house()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        // Spelt out rather than inline, for the reason `StationsModelTests`
        // spells it out: `??` erases the `@Sendable` of a closure literal.
        let immediate: @Sendable (Int64) async throws -> Void = { _ in }
        let model = StationsModel(
            in: container,
            playback: playback,
            wait: immediate,
            clock: { 1_793_613_840_000 }
        )
        return (model, park)
    }

    private func started(_ model: StationsModel) async {
        model.start()
        await settleUntil(
            { model.favorites.state.loaded && model.badge.data != nil && model.recent.data != nil },
            "the three reads"
        )
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

    // MARK: - 1. a witness handed in later

    /// A witness handed in **after** the model was built is the one the next
    /// tap reaches.
    ///
    /// Review 2's second finding: this screen was the one of the five that
    /// captured its witness once. ``StationsView`` builds the model on the
    /// first appearance from whatever ``ShellModel/player`` was at that
    /// instant, and nothing re-asked — so a player that arrived later, or a
    /// house move that brought a different one, left a stale `canPlay` and a
    /// stale engine behind the rows. ``StationsModel/use(_:)`` is the seam the
    /// view now calls in its `.task` and in its `serverURL` hook.
    ///
    /// It discriminates in both directions: the gate and the subtitle's
    /// instruction have to appear, and the **new** witness has to be the one
    /// that is played through — a `use(_:)` that stored the value and left the
    /// old one in the play path would pass the first assertions and fail the
    /// last.
    func testAWitnessHandedInLaterIsTheOneThatPlays() async throws {
        let silent = StationsPlaybackSpy(canPlay: false)
        let (model, park) = rig(playback: silent)
        // Nothing is held in this case: `/play` answers as soon as it is
        // asked.
        park.release.set(true)
        await started(model)
        let row = try XCTUnwrap(model.favorites.state.items.first)
        XCTAssertFalse(model.canPlayAnything)
        XCTAssertFalse(model.canPlay(row), "the silent witness is the one in the gate")
        // The header this build draws, which `StationsModelTests` measured
        // against `playbackNote` until that string was deleted: a page whose
        // rows do not play must not tell anybody to tap one.
        XCTAssertFalse(
            model.subtitle.contains("tap a station"),
            "a page whose rows do not play told somebody to tap one: \(model.subtitle)"
        )

        let engine = StationsPlaybackSpy(canPlay: true)
        model.use(engine)

        XCTAssertTrue(model.canPlayAnything)
        XCTAssertTrue(
            model.subtitle.contains("tap a station to play it"),
            "the header still says the rows do not play: \(model.subtitle)"
        )
        model.play(row)
        await settleUntil({ !engine.played.current.isEmpty }, "the play")
        // The SAVED row's id, not `row.id`: `/play` answers the row the
        // server stamped and that is what the engine is handed, which
        // `StationsModelTests.testPlayingPostsPlayAndHandsTheAnsweredRowToTheEngine`
        // pins for its own stub.
        XCTAssertEqual([savedID], engine.played.current,
                       "the tap went somewhere other than the witness handed in last")
        XCTAssertTrue(silent.played.current.isEmpty,
                      "the stale witness is still in the play path")
    }

    // MARK: - 2. the witness the tap was made against

    /// The station goes to the witness the **tap** was made against, not to
    /// whichever one the view has handed in by the time the server answers.
    ///
    /// The mechanism, exercised the only way that discriminates: `POST /play`
    /// is **parked inside the dispatcher**, ``StationsModel/use(_:)`` is called
    /// while it is parked, and then it is released. A case that re-pointed the
    /// witness before or after the round trip would pass against a
    /// `play(_:)` that read `self.playback` after the `await` — which is
    /// precisely the sabotage `StationsModelTests` could not catch.
    ///
    /// Why this way round. ``StationsModel/play(_:)`` asks
    /// ``StationsPlayback/canPlay`` of the witness it has, refuses to POST at
    /// all when that is false, and then hands the answered row to the engine:
    /// the gate and the handover are one decision, and a row that was
    /// admitted by one engine must not be played by another. The epoch guard
    /// covers the other half — a `/play` answered after a **house** move is
    /// dropped altogether — and it cannot cover this one, because re-pointing
    /// the witness on an appearance moves no house.
    ///
    /// `StationsView` re-points on every appearance, so the window is
    /// ordinary rather than exotic: tap a station, switch tab, come back
    /// while the POST is still open.
    func testTheWitnessTheTapWasMadeAgainstIsTheOneThatPlays() async throws {
        let tapped = StationsPlaybackSpy(canPlay: true)
        let (model, park) = rig(playback: tapped)
        await started(model)
        let row = try XCTUnwrap(model.favorites.state.items.first)

        model.play(row)
        await settle(for: park.arrived, what: "the /play POST reaching the house")

        // Inside the round trip: the screen appeared again and handed the
        // model the witness it built this time.
        let reappeared = StationsPlaybackSpy(canPlay: true)
        model.use(reappeared)
        park.release.set(true)

        await settleUntil({ !tapped.played.current.isEmpty || !reappeared.played.current.isEmpty },
                          "the play landing on one witness or the other")
        XCTAssertFalse(
            park.ranOut.current,
            "the park timed out, so the POST never answered and this asserts nothing"
        )
        XCTAssertEqual(
            [savedID], tapped.played.current,
            "the station was handed to a witness that arrived after the tap — "
                + "`let playback = self.playback` before the await is what stops that"
        )
        XCTAssertTrue(
            reappeared.played.current.isEmpty,
            "the witness handed in mid-flight played a station nobody tapped on it"
        )
        XCTAssertEqual(
            StationsStrings.playing("Catalogue Ibis 01"), model.toast,
            "the toast is the answered row's name"
        )
    }

    /// And the **gate** is the one that was asked, not the one that arrived:
    /// a witness that cannot play, handed in while a POST is open, does not
    /// get the row either.
    ///
    /// The same mechanism one notch further: this is the state the owner's
    /// no-misleading-controls rule is about. ``StationsNoPlayback`` is what a
    /// build with no audio engine holds, and handing it a station would be a
    /// silent play — the one visible effect of which is a history entry for
    /// something nobody heard.
    func testASilentWitnessHandedInMidFlightIsNotPlayedThrough() async throws {
        let tapped = StationsPlaybackSpy(canPlay: true)
        let (model, park) = rig(playback: tapped)
        await started(model)
        let row = try XCTUnwrap(model.favorites.state.items.first)

        model.play(row)
        await settle(for: park.arrived, what: "the /play POST reaching the house")

        let silent = StationsPlaybackSpy(canPlay: false)
        model.use(silent)
        park.release.set(true)

        // Either witness, so a broken capture fails on the assertion below
        // rather than spending twenty thousand yields finding out that the
        // one it should have reached was never touched.
        await settleUntil({ !tapped.played.current.isEmpty || !silent.played.current.isEmpty },
                          "the play landing on one witness or the other")
        XCTAssertFalse(park.ranOut.current, "the park timed out, so this asserts nothing")
        XCTAssertEqual([savedID], tapped.played.current)
        XCTAssertTrue(
            silent.played.current.isEmpty,
            "a witness that says it cannot play was handed a station anyway"
        )
        // And the model now draws the silent build's header, because that IS
        // the witness it holds from here on: the capture is about the row in
        // flight and nothing else.
        XCTAssertFalse(model.canPlayAnything)
    }
}
