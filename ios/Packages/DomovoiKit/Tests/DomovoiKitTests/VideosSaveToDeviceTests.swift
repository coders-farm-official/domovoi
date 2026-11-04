import Foundation
import XCTest
@testable import DomovoiKit

/// The **Videos** half of D47: what `save to device` on the feed's options
/// sheet actually asks the house for, what it calls the copy, and what it
/// lets go of when the app moves house.
///
/// ### Why this file exists at all
///
/// It is REVIEW-2's first must. `VideosModel.saveToDevice(_:)` and the
/// `saves.serverChanged()` line in `VideosModel.serverChanged()` were covered
/// by nothing: `grep -rn saveToDevice` over the whole test tree returned zero
/// hits, so three one-line regressions left the suite green —
///
///  1. `VideoPath.stream(video, download: true)` changed to `download: false`,
///     which is the one Android-match the brief named (`VideosScreen.kt:222`);
///  2. the ``FileSaveModel/safeName(_:)`` hardening dropped, so a name the
///     **server** chose decides where this app writes;
///  3. `saves.serverChanged()` deleted, so a video half-fetched from a house
///     that has gone is handed over as the new one's
///     ([[read-after-await-wrong-key]]).
///
/// `VideoModelsTests.swift:363` pins the path *builder* and never that the
/// caller passes `true`, and the stub house's `/api/videos/stream` ignores the
/// flag — so no UI case could see it either.
/// `testTransfers_AVideoIsSavedFromItsOptionsSheet` asserts the sentence, the
/// stop, the share sheet and the sheet not re-presenting, and never the
/// request. Files has the mirror of all three in `FilesModelTests`; Videos had
/// none.
///
/// ### The rig
///
/// `VideosModel`'s own `saves` is built in its initialiser from the container
/// (`SaveToDeviceModel(in:)`), so it cannot be injected: the only seam is the
/// socket, and these cases read the request off ``StubURLProtocol`` instead of
/// off a closure. That is the right level for this file anyway — the claim is
/// about the **request** the screen makes, which is what a sabotaged
/// `download:` flag changes and what a stubbed fetch would hide.
///
/// Nothing calls `start()`: the grid and the strip are other cases' subject,
/// and a model that has not been started makes exactly one request per save,
/// which is the one being read.
@MainActor
final class VideosSaveToDeviceTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    private func rig(deviceID: String? = "ios-1") -> VideosModel {
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        return VideosModel(in: container, deviceID: deviceID)
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
        XCTFail("\(what) never happened", file: file, line: line)
    }

    // MARK: - what the house is asked for

    /// **`download=1`, and the name hardened before anything is written.**
    ///
    /// The row is the shape a hostile or merely broken server can send: a
    /// `name` with a path in it and a colon, which is a path separator to some
    /// of macOS's own APIs. What reaches the disk is the last component with
    /// the colon replaced, and what reaches the socket is the stream route
    /// with the attachment flag on it.
    func testAVideoIsAskedForAsAnAttachmentAndNamedSafely() async throws {
        StubURLProtocol.enqueue(.json("the film's bytes"))
        let model = rig()
        let video = VideoRow(
            libraryID: "core:videos",
            rel: "clips/a long one.mkv",
            name: "../../etc/pa:sswd.mkv"
        )

        model.saveToDevice(video)
        XCTAssertEqual(
            .fetching("pa_sswd.mkv", nil), model.saves.state,
            "the name the server sent went to disk as it was sent"
        )

        await settleUntil(
            { if case .ready = model.saves.state { return true } else { return false } },
            "the saved film"
        )
        guard case .ready(let url, let name) = model.saves.state else {
            return XCTFail("the save never finished")
        }
        XCTAssertEqual("pa_sswd.mkv", name)
        XCTAssertEqual(name, url.lastPathComponent, "the copy is written under the hardened name")
        XCTAssertEqual("the film's bytes", try String(contentsOf: url, encoding: .utf8))

        let asked = try XCTUnwrap(StubURLProtocol.recorded.first?.url)
        XCTAssertEqual("/api/videos/stream", asked.path)
        // `absoluteString` and not `query`: the identity pair is
        // percent-encoded by ``VideoPath/esc(_:)`` and the assertions below
        // are about the encoded form, which is the thing that goes down the
        // socket.
        let query = asked.absoluteString
        // **The assertion the sabotage was traced against.** Android asks for
        // the attachment form (`VideosScreen.kt:222`'s
        // `streamPath(v, download = true)`); without it the server answers for
        // inline playback and the share sheet is handed a film the person
        // asked to keep, under whatever disposition the player wanted.
        XCTAssertTrue(
            query.contains("download=1"),
            "the save asked for inline playback rather than an attachment: \(query)"
        )
        XCTAssertTrue(query.contains("library_id=core%3Avideos"), "no library in: \(query)")
        XCTAssertTrue(
            query.contains("path=clips%2Fa%20long%20one.mkv"),
            "no path in: \(query)"
        )

        model.saves.taken()
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: url.path),
            "the household's film is left in this app's container"
        )
    }

    /// A row with no `name` at all still gets a filename, and it is the app's
    /// word rather than the empty string.
    ///
    /// `/api/videos/list` computes `name` for every row, so this is the older
    /// server and the malformed answer, not an ordinary one — and an empty
    /// name would make the destination the spool **directory** itself, which
    /// fails the move with "couldn't write it onto this phone" about a phone
    /// with nothing wrong with it.
    func testAVideoWithNoNameOfItsOwnIsStillCalledSomething() {
        let model = rig()
        model.saveToDevice(VideoRow(libraryID: "usb", rel: "phone/VID_0001.mp4", name: ""))
        XCTAssertEqual(.fetching(VideoStrings.unnamedVideo, nil), model.saves.state)
        model.saves.cancel()
    }

    // MARK: - moving house, inside the round trip

    /// **A film half-fetched from a house that has gone is not handed over.**
    ///
    /// `VideosModel.serverChanged()` forgets the feed, the centring, the tap
    /// in flight and the headings' counts; the save is the fifth thing it
    /// holds and it is the only one that can arrive **after** the switch. A
    /// library id and a path are both reusable, so the late answer looks
    /// exactly right: a film from a server that never had one.
    ///
    /// The switch happens **inside** the round trip — the request is parked at
    /// the stub when `serverChanged()` is called — which is the only position
    /// from which this case can fail if the line is deleted. `FilesModelTests`
    /// has the same assertion for Files
    /// (`testMovingHouseForgetsEverything:1040`); `VideosModelTests`'
    /// `testMovingHouseLetsGoOfItsOwnState` asserts the other four and is
    /// another unit's file to extend, so the fifth is here.
    func testAFilmThatLandsAfterTheHouseChangedIsNotHandedOver() async throws {
        let park = parkRequest(to: "/api/videos/stream", answering: { .json("the old house's film") })
        let model = rig()
        model.saveToDevice(VideoRow(libraryID: "core:videos", rel: "clips/x.mkv", name: "x.mkv"))
        XCTAssertEqual(.fetching("x.mkv", nil), model.saves.state)
        await settleUntil({ park.arrived.current }, "the request reaching the house")

        model.serverChanged()
        XCTAssertEqual(
            .idle, model.saves.state,
            "a film from the house the app has left is still on screen as a save in progress"
        )

        park.release.set(true)
        for _ in 0..<2_000 { await Task.yield() }
        XCTAssertFalse(park.ranOut.current, "the park outlasted the session timeout — nothing was answered")
        XCTAssertEqual(
            .idle, model.saves.state,
            "the old house's film was handed over under the new house"
        )
    }
}
