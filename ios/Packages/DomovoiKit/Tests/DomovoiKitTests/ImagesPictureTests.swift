import Foundation
import XCTest
@testable import DomovoiKit

/// ``ImagesPictureModel``: the viewer behind a generated picture.
///
/// Its own file rather than more of `ImagesTests.swift`, which is already
/// thirty cases of the screen's data layer — and because every case here
/// injects a fetch closure instead of standing up a house, so none of that
/// file's rig is needed. ``Shared`` and ``settleYieldLimit`` are the test
/// target's own (`Shared.swift`); `VideoPosterStoreTests` drives the same
/// kind of `@Sendable` fetch the same way, and this follows it rather than
/// inventing a second box.
///
/// Four things are worth asserting, and they are the four this type exists
/// for: the path it asks for (the one escape this screen depends on), the
/// 64-byte floor, the error's **own words** reaching the sheet, and a retry
/// that cannot be beaten by the fetch it replaced.
@MainActor
final class ImagesPictureTests: XCTestCase {

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the viewer",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    func testItAsksForThePluginsFileRouteWithTheNameEscapedWhole() async {
        let asked = Shared<[String]>([])
        let model = ImagesPictureModel(name: "a kingfisher/01.png") { path in
            asked.mutate { $0.append(path) }
            return imagesBigEnough()
        }
        model.read()
        await settleUntil { model.load != .loading }

        // The **slash inside the name is escaped**, because `Uri.encode` with
        // no allowed set escapes it too (`ImagesScreen.kt:125`) and the name
        // is one opaque path segment on the wire. A `/` left through asks for
        // a route that does not exist — a quiet 404 — and the inverse would
        // be a quiet traversal. The space is escaped for the same reason.
        XCTAssertEqual(
            ["/api/plugins/imagegen/file/a%20kingfisher%2F01.png"],
            asked.current
        )
    }

    func testBytesTooFewToBeAPictureAreNotDrawnAsOne() async {
        let model = ImagesPictureModel(name: "robot-01.png") { _ in
            // One byte under the floor. A reverse proxy in front of a house
            // serves an error page with a 200, and a 204 has no body at all;
            // both arrive here as "success".
            Data(repeating: 0x42, count: ImagesPictureModel.smallestImage - 1)
        }
        model.read()
        await settleUntil { model.load != .loading }

        XCTAssertEqual(.failed(ImagesStrings.couldNotShowPicture), model.load)
        // One sentence for one failure, across both screens: this **is**
        // ``FileStrings/notAPicture`` and not a second wording of it.
        XCTAssertEqual(FileStrings.notAPicture, ImagesStrings.couldNotShowPicture)
    }

    func testAFetchThatFailedCarriesTheServersOwnWordsIntoTheSheet() async {
        let said = "404: {\"detail\":\"no such file\"}"
        let model = ImagesPictureModel(name: "robot-01.png") { _ in
            throw DomovoiError.refused(ApiFailure(status: 404, message: said))
        }
        model.read()
        await settleUntil { model.load != .loading }

        guard case .failed(let shown) = model.load else {
            return XCTFail("a 404 must be a failure the sheet can show, not a blank picture")
        }
        // The whole reason this type exists beside ``VideoPosterStore``: a
        // tile draws a glyph and says nothing, and a sheet somebody opened on
        // purpose has to say what went wrong.
        XCTAssertTrue(shown.contains("no such file"), shown)
        XCTAssertEqual(ImagesStrings.couldNotLoadPicture(said), shown)
    }

    /// The retry cannot be beaten by the fetch it replaced.
    ///
    /// [[read-after-await-wrong-key]] in its smallest form: two fetches in
    /// flight for one `load`, and the one that must win is the **later**. The
    /// first is held until the retry's answer is already on screen, then
    /// released, so the race runs in the order that breaks a model with no
    /// epoch rather than in whichever order the scheduler happens to pick.
    func testASlowFirstFetchCannotOverwriteARetrysAnswer() async {
        let release = Shared<Bool>(false)
        let calls = Shared<Int>(0)
        let model = ImagesPictureModel(name: "robot-01.png") { _ in
            let mine = calls.mutate { (count: inout Int) -> Int in count += 1; return count }
            if mine == 1 {
                for _ in 0..<settleYieldLimit {
                    if release.current { break }
                    await Task.yield()
                }
                // The old answer, and the one that must land nowhere.
                throw DomovoiError.unreachable("house A dropped the connection")
            }
            return imagesBigEnough()
        }

        model.read()
        await settleUntil { calls.current == 1 }

        model.read()
        await settleUntil { calls.current == 2 }
        await settleUntil { model.load != .loading }
        XCTAssertEqual(.bytes(imagesBigEnough()), model.load, "the retry's answer is the one on screen")

        release.set(true)
        // Give the first continuation every chance to do the wrong thing.
        for _ in 0..<2000 { await Task.yield() }
        XCTAssertEqual(
            .bytes(imagesBigEnough()),
            model.load,
            "the first fetch's failure landed after the retry's success and must be discarded"
        )
    }

    func testTheDialogsCancelIsItsOwnWordAndNotTheLanesCancel() {
        // Same word, two Kotlin files, either rewordable without the other:
        // `Dialogs.kt:41` is "don't delete anything" and
        // `ImagesScreen.kt:353` is "stop making this picture". The assertion
        // is that both exist, not that they differ — what this catches is a
        // merge that deleted one and pointed its call site at the other.
        XCTAssertEqual("cancel", ImagesStrings.dialogCancel)
        XCTAssertEqual("cancel", ImagesStrings.cancel)
    }

    func testTheHandOverFailureSaysSomethingAndInterpolatesNothing() {
        // No server detail to pass on — the failure is inside this app's own
        // container — so the sentence has to stand alone, and must not be an
        // empty string a view would draw as a blank line.
        XCTAssertFalse(ImagesStrings.couldNotHandOverPicture.isEmpty)
        XCTAssertFalse(ImagesStrings.couldNotHandOverPicture.contains("("))
    }
}

/// Enough bytes to clear the model's floor, and not an image — nothing in
/// this package decodes one: DomovoiKit has no UIKit (it has to build and
/// test on macOS), so the decode is the view's job and the floor is the
/// model's.
///
/// A **file-level** function and not a static on the case class, which is
/// `@MainActor`: the fetch is a non-isolated `@Sendable` closure, and calling
/// an actor-isolated member from inside one does not compile under Swift 6
/// strict concurrency. `VideoPosterStoreTests` puts its own on a
/// `Sendable` rig class for the same reason.
private func imagesBigEnough() -> Data {
    Data(repeating: 0x42, count: ImagesPictureModel.smallestImage)
}
