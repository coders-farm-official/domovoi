import Foundation
import XCTest
@testable import DomovoiKit

/// The save side of D47: the states, the epoch, the cancel, and the file this
/// app does not leave behind.
///
/// The fetch seam takes a **destination** and answers a URL, which is the
/// shape that cannot hold a file in memory; every case here drives it with a
/// gate, so the test is inside the round trip rather than before or after it.
/// That is the only position from which the epoch means anything
/// ([[read-after-await-wrong-key]]).
@MainActor
final class SaveToDeviceModelTests: XCTestCase {

    // MARK: - the states

    func testASaveGoesFetchingThenReadyAndTheFileIsThere() async {
        let gate = Gate()
        let model = SaveToDeviceModel(fetch: { _, into, _ in
            await gate.wait()
            try Data("a film".utf8).write(to: into, options: .atomic)
            return into
        })

        model.save(path: "/api/files/download?library_id=core%3Amusic&path=film.mkv", named: "film.mkv")
        XCTAssertEqual(.fetching("film.mkv", nil), model.state, "no percent until the transport reports one")

        gate.open()
        await settle({ if case .ready = model.state { return true } else { return false } }, "the file")
        guard case .ready(let url, let name) = model.state else { return XCTFail("not ready") }
        XCTAssertEqual("film.mkv", name)
        XCTAssertEqual("a film", (try? String(contentsOf: url, encoding: .utf8)))
        XCTAssertEqual(
            model.slot, url.deletingLastPathComponent().lastPathComponent,
            "the copy goes in THIS saver's own directory, not in a shared one"
        )
        XCTAssertEqual(
            FileSaveModel.spool,
            url.deletingLastPathComponent().deletingLastPathComponent().lastPathComponent,
            "and that directory is inside the spool"
        )

        model.taken()
        XCTAssertEqual(.idle, model.state)
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: url.path),
            "the household's file is not left in this app's container"
        )
    }

    /// The percentage reaches the state.
    ///
    /// The transport reports from one of its own threads, so the number goes
    /// through a lock and a hop to the main actor; what this case is really
    /// about is that the hop arrives, because a determinate bar that never
    /// updates is worse than an indeterminate one.
    func testThePercentTheTransportReportsBecomesTheStatesShare() async {
        let gate = Gate()
        let ticks = Shared<[(Int64, Int64)]>([])
        let model = SaveToDeviceModel(fetch: { _, into, onProgress in
            for tick in ticks.current { onProgress(tick.0, tick.1) }
            await gate.wait()
            try Data("x".utf8).write(to: into, options: .atomic)
            return into
        })
        ticks.set([(0, 400), (100, 400), (300, 400)])

        model.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        await settle({ model.state == .fetching("film.mkv", 0.75) }, "the last tick (at \(model.state))")

        gate.open()
        await settle({ if case .ready = model.state { return true } else { return false } }, "the file")
        model.taken()
    }

    /// A transport that declares no length leaves the bar indeterminate
    /// rather than pinning it at nought, which reads as a stall.
    func testATransferWithNoDeclaredTotalKeepsAnIndeterminateBar() async {
        let gate = Gate()
        let model = SaveToDeviceModel(fetch: { _, into, onProgress in
            onProgress(900, -1)
            await gate.wait()
            try Data("x".utf8).write(to: into, options: .atomic)
            return into
        })
        model.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        // Nothing to settle **for**: the assertion is that a tick arrived and
        // changed nothing, so the yields are spent and then the state is read.
        for _ in 0..<200 { await Task.yield() }
        XCTAssertEqual(.fetching("film.mkv", nil), model.state)
        gate.open()
        await settle({ if case .ready = model.state { return true } else { return false } }, "the file")
        model.taken()
    }

    // MARK: - the epoch

    /// **The defect this type's note is about.** A save that lands after the
    /// app moved house must not be handed over, and must not land on top of
    /// the new house's own save.
    ///
    /// A library id and a path are both reusable, so the late answer looks
    /// exactly right: a file called `film.mkv` from a server that never had
    /// one. The switch happens **inside** the first round trip — the gate is
    /// still shut when `serverChanged()` is called — which is the only
    /// position from which this case can fail if the guard is removed.
    func testAFileThatLandsAfterTheHouseChangedIsNotHandedOver() async {
        let first = Gate()
        let second = Gate()
        let answered = Shared(0)
        let model = SaveToDeviceModel(fetch: { _, into, onProgress in
            let mine = answered.current
            answered.set(mine + 1)
            await (mine == 0 ? first : second).wait()
            // A progress tick from the OLD house, after the switch: it must
            // not move the new one's bar either.
            onProgress(5, 10)
            try Data(mine == 0 ? "the old house".utf8 : "the new house".utf8)
                .write(to: into, options: .atomic)
            return into
        })

        model.save(path: "/api/files/download?library_id=core%3Amusic&path=film.mkv", named: "film.mkv")
        XCTAssertEqual(.fetching("film.mkv", nil), model.state)

        model.serverChanged()
        XCTAssertEqual(.idle, model.state, "nothing from the old house is still on screen")

        // The new house asks for the same path, which is the whole point.
        model.save(path: "/api/files/download?library_id=core%3Amusic&path=film.mkv", named: "film.mkv")
        XCTAssertEqual(.fetching("film.mkv", nil), model.state)

        first.open()
        for _ in 0..<500 { await Task.yield() }
        XCTAssertEqual(
            .fetching("film.mkv", nil), model.state,
            "the old house's answer was handed over, or its progress moved the new house's bar"
        )

        second.open()
        await settle({ if case .ready = model.state { return true } else { return false } }, "the new file")
        guard case .ready(let url, _) = model.state else { return XCTFail("not ready") }
        XCTAssertEqual("the new house", (try? String(contentsOf: url, encoding: .utf8)))
        model.taken()
    }

    /// `serverChanged()` while a file is **ready** removes it too: it is the
    /// old house's file, sitting in this app's container.
    func testAServerChangeDropsAFileAlreadyFetched() async {
        let model = SaveToDeviceModel(fetch: { _, into, _ in
            try Data("x".utf8).write(to: into, options: .atomic)
            return into
        })
        model.save(path: "/api/files/download?path=a.mp3", named: "a.mp3")
        await settle({ if case .ready = model.state { return true } else { return false } }, "the file")
        guard case .ready(let url, _) = model.state else { return XCTFail("not ready") }

        model.serverChanged()
        XCTAssertEqual(.idle, model.state)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }

    // MARK: - the cancel

    /// A stop is `.idle`, not `.failed`: the person asked for it, and a red
    /// sentence about a thing somebody chose is noise.
    func testStoppingASaveGoesQuietRatherThanRed() async {
        let gate = Gate()
        let model = SaveToDeviceModel(fetch: { _, into, _ in
            await gate.wait()
            try Data("x".utf8).write(to: into, options: .atomic)
            return into
        })
        model.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        model.cancel()
        XCTAssertEqual(.idle, model.state)
        model.cancel()
        XCTAssertEqual(.idle, model.state, "a second stop is safe")

        gate.open()
        for _ in 0..<500 { await Task.yield() }
        XCTAssertEqual(.idle, model.state, "the cancelled transfer's answer was handed over anyway")
    }

    /// The transport's own "cancelled" is not a failure to say out loud.
    ///
    /// `transportError` maps `URLError.cancelled` to `.unreachable("cancelled")`,
    /// which is right for a read and wrong for a save the person stopped:
    /// "couldn't save: cancelled" is the app blaming them for a button they
    /// pressed.
    func testATransportCancelDoesNotBecomeASentence() async {
        let model = SaveToDeviceModel(fetch: { _, _, _ in
            throw DomovoiError.unreachable("cancelled")
        })
        model.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        await settle({ model.state == .idle }, "the quiet end (at \(model.state))")
    }

    /// Every other transport failure **is** said, with the server's own words
    /// where it gave any.
    func testAFailureSaysWhatWentWrong() async {
        let model = SaveToDeviceModel(fetch: { _, _, _ in
            throw DomovoiError.unreachable("no route to host")
        })
        model.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        await settle({ model.state == .failed(TransferStrings.couldNotSave("no route to host")) },
                     "the failure (at \(model.state))")

        let refused = SaveToDeviceModel(fetch: { _, _, _ in
            let body = "{\"detail\":\"not in any library\"}"
            throw DomovoiError.refused(ApiFailure(status: 403, message: "403: \(body)", body: body))
        })
        refused.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        await settle({
            if case .failed(let said) = refused.state { return said.contains("not in any library") }
            return false
        }, "the server's own words (at \(refused.state))")
    }

    /// A failure does not leave the control stuck: the next tap starts a save.
    ///
    /// `failed` draws the tap again for exactly this reason — a disabled
    /// button in that state would be a control that cannot do what it says
    /// ([[no-misleading-controls]]).
    func testASaveCanBeTriedAgainAfterItFailed() async {
        let fail = Shared(true)
        let model = SaveToDeviceModel(fetch: { _, into, _ in
            if fail.current { throw DomovoiError.unreachable("no route to host") }
            try Data("second time".utf8).write(to: into, options: .atomic)
            return into
        })
        model.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        await settle({ if case .failed = model.state { return true } else { return false } }, "the failure")

        fail.set(false)
        model.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        await settle({ if case .ready = model.state { return true } else { return false } }, "the retry")
        model.taken()
    }

    // MARK: - one at a time

    /// A second tap while one is in flight is ignored.
    ///
    /// Not queued: the share sheet that opens can only be about one file, and
    /// the person has not said which. Android queues because the system owns
    /// the notification; here the sheet is the completion.
    func testASecondTapWhileFetchingIsIgnored() async {
        let gate = Gate()
        let asked = Shared<[String]>([])
        let model = SaveToDeviceModel(fetch: { path, into, _ in
            asked.set(asked.current + [path])
            await gate.wait()
            try Data("x".utf8).write(to: into, options: .atomic)
            return into
        })
        model.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        model.save(path: "/api/files/download?path=other.mkv", named: "other.mkv")
        XCTAssertEqual(.fetching("film.mkv", nil), model.state, "the first save is the one on screen")

        gate.open()
        await settle({ if case .ready = model.state { return true } else { return false } }, "the file")
        XCTAssertEqual(1, asked.current.count, "the second tap started a transfer")
        guard case .ready(_, let name) = model.state else { return XCTFail("not ready") }
        XCTAssertEqual("film.mkv", name)
        model.taken()
    }

    /// The spool holds **one** file: a save does not accumulate somebody's
    /// gigabytes in a directory iOS clears "at some point".
    ///
    /// Driven by leaving a file there rather than by saving twice, because
    /// two saves cannot overlap (the case above) and the one that can leave a
    /// file behind is the person who closes the share sheet without sharing
    /// — or an app that was killed mid-save. Either way the next save is what
    /// clears it.
    func testASaveEmptiesTheSpoolOnTheWayIn() async throws {
        let model = SaveToDeviceModel(fetch: { _, into, _ in
            try Data("x".utf8).write(to: into, options: .atomic)
            return into
        })
        let spool = try FileSaveModel.emptySpool(slot: model.slot)
        let stray = spool.appendingPathComponent("left over from last time.mkv", isDirectory: false)
        try Data(count: 4096).write(to: stray, options: .atomic)
        XCTAssertTrue(FileManager.default.fileExists(atPath: stray.path))

        model.save(path: "/api/files/download?path=b.mp3", named: "b.mp3")
        await settle({ if case .ready = model.state { return true } else { return false } }, "the file")
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: stray.path),
            "a file from an earlier save is still in the spool"
        )
        model.taken()
    }

    /// **Two savers do not clear each other's directory.**
    ///
    /// REVIEW-1 finding 1, and the shape of the bug it is about: `emptySpool`
    /// deleted one `domovoi-saved` at the start of **every** save, while
    /// ``FilesModel`` holds a saver and ``VideosModel`` holds another and a
    /// `TabView` keeps both alive. Switching tabs and tapping save therefore
    /// removed the directory the first screen's half-finished download was
    /// pointed at: `didFinishDownloadingTo`'s move threw, `downloadFile`
    /// turned that into ``TransferStrings/couldNotWrite`` and Files told the
    /// person to check the storage on a phone with plenty of it.
    ///
    /// Driven from **inside** the first save's round trip, which is the only
    /// position the bug exists in: the second save is started while the first
    /// one is waiting, and then the first one is let through and asked
    /// whether its destination survived.
    func testTwoSaversDoNotClearEachOthersSpool() async throws {
        let held = Gate()
        let films = SaveToDeviceModel(fetch: { _, into, _ in
            // The destination's directory is read **after** the wait, as the
            // real transport reads it: `URLSession` moves the finished file
            // into place when the body has arrived, not when the task was
            // made.
            await held.wait()
            guard FileManager.default.fileExists(atPath: into.deletingLastPathComponent().path) else {
                // What the platform does: no directory, no move. The wording
                // is the client's own for a failed move.
                throw DomovoiError.unreachable(TransferStrings.couldNotWrite)
            }
            try Data("a film".utf8).write(to: into, options: .atomic)
            return into
        })
        let videos = SaveToDeviceModel(fetch: { _, into, _ in
            try Data("a clip".utf8).write(to: into, options: .atomic)
            return into
        })
        XCTAssertNotEqual(films.slot, videos.slot, "two savers were given one directory")

        films.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        // The other screen, mid-flight. This is the tap that used to break
        // the save above.
        videos.save(path: "/api/videos/stream?id=7&download=1", named: "a clip.mp4")
        await settle({ if case .ready = videos.state { return true } else { return false } },
                     "the second screen's file")

        held.open()
        await settle({ if case .ready = films.state { return true } else { return false } },
                     "the first screen's file")
        guard case .ready(let film, _) = films.state else {
            return XCTFail("the first save failed: \(films.state)")
        }
        XCTAssertEqual("a film", (try? String(contentsOf: film, encoding: .utf8)))
        guard case .ready(let clip, _) = videos.state else { return XCTFail("not ready") }
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: clip.path),
            "the second screen's finished file was deleted by the first screen's save"
        )
        films.taken()
        videos.taken()
    }

    /// A failed write says it **once**.
    ///
    /// ``DomovoiClient/downloadFile(path:into:trackPairing:onProgress:)``
    /// throws ``TransferStrings/couldNotWrite``, which is already a whole
    /// sentence about saving; wrapping it in
    /// ``TransferStrings/couldNotSave(_:)`` read "couldn't save: couldn't
    /// write it onto this phone — check your storage" (REVIEW-1 note 10).
    /// Every other failure is still wrapped, which the second half asserts.
    func testAFailedWriteIsNotSaidTwice() async {
        let model = SaveToDeviceModel(fetch: { _, _, _ in
            throw DomovoiError.unreachable(TransferStrings.couldNotWrite)
        })
        model.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        await settle({ if case .failed = model.state { return true } else { return false } }, "the refusal")
        XCTAssertEqual(.failed(TransferStrings.couldNotWrite), model.state)

        let other = SaveToDeviceModel(fetch: { _, _, _ in
            throw DomovoiError.noServer
        })
        other.save(path: "/api/files/download?path=film.mkv", named: "film.mkv")
        await settle({ if case .failed = other.state { return true } else { return false } }, "the refusal")
        guard case .failed(let why) = other.state else { return XCTFail("not failed") }
        XCTAssertTrue(why.hasPrefix("couldn't save: "), "an ordinary failure lost its lead-in: \(why)")
    }

    // MARK: - the helpers

    private func settle(
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
}

/// A round trip the test holds open.
///
/// `await gate.wait()` is where the fetch sits while the case does whatever it
/// is about — change house, stop the save, tap again — so the assertion is
/// made from **inside** the transfer and not around it.
private final class Gate: @unchecked Sendable {
    private let lock = NSLock()
    private var isOpen = false

    func open() { lock.withLock { isOpen = true } }

    func wait() async {
        while !lock.withLock({ isOpen }) { await Task.yield() }
    }
}
