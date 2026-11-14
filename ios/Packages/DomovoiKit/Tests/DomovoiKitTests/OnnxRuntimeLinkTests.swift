import Foundation
import XCTest
@testable import DomovoiKit

/// ONNX Runtime is linked, loads a model and computes with it — on the Mac,
/// under `swift test`, through the same ``OnnxModel`` the app's room mode
/// runs its networks on (ROOMMODE-MAP §6).
///
/// The model is `Fixtures/tiny_add.onnx`, 107 bytes that
/// `ios/scripts/make-onnx-fixture.py` writes by hand (one `Add` node, float
/// [3] + float [3]): ours, with no licence question and no download.
///
/// What each case fails on:
/// - the package not linking at all fails the BUILD, which is the first thing
///   this file exists to prove on the Mac (the macOS slice of the pinned
///   xcframework);
/// - a library that links but is not the pinned one fails the version case;
/// - a model that loads but does not compute (or computes the wrong thing)
///   fails the arithmetic case, which was proved red on a throwaway branch
///   whose fixture said `Sub` (batch report).
final class OnnxRuntimeLinkTests: XCTestCase {

    private func fixture() throws -> URL {
        guard let url = Bundle.module.url(forResource: "tiny_add", withExtension: "onnx", subdirectory: "Fixtures") else {
            XCTFail("Fixtures/tiny_add.onnx is not in the test bundle")
            throw OnnxModel.Failure(note: "no fixture")
        }
        return url
    }

    func testTheLinkedRuntimeIsThePinnedOne() {
        XCTAssertEqual("1.24.2", OnnxModel.runtimeVersion, "Package.swift pins 1.24.2 exactly")
    }

    func testTheTinyModelLoadsWithItsNames() throws {
        let model = try OnnxModel(contentsOf: fixture())
        XCTAssertEqual(["X", "Y"], model.inputNames)
        XCTAssertEqual(["Z"], model.outputNames)
    }

    func testTheTinyModelAdds() throws {
        let model = try OnnxModel(contentsOf: fixture())
        let out = try model.run(
            ["X": .float([1, 2, 3], shape: [3]), "Y": .float([10, 20, 30], shape: [3])],
            outputs: ["Z"]
        )
        XCTAssertEqual([11, 22, 33], out["Z"])
        let shaped = try model.runShaped(
            ["X": .float([0.5, -1, 4], shape: [3]), "Y": .float([0.25, 1, -4], shape: [3])],
            output: "Z"
        )
        XCTAssertEqual([0.75, 0, 0], shaped.values)
        XCTAssertEqual([3], shaped.shape)
    }

    /// A model that is not there is a sentence, not a crash: the room says
    /// "could not load the wake word" from it (`RoomEngine.kt:718`).
    func testAMissingModelIsAFailureWithAName() {
        let missing = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("no-such-model.onnx")
        XCTAssertThrowsError(try OnnxModel(contentsOf: missing)) { error in
            let note = (error as? OnnxModel.Failure)?.note ?? ""
            XCTAssertTrue(note.contains("no-such-model.onnx"), note)
        }
    }

    /// A wrong input is ONNX Runtime's refusal, passed on, never a trap.
    func testAWrongShapeIsAFailureNotATrap() throws {
        let model = try OnnxModel(contentsOf: fixture())
        XCTAssertThrowsError(try model.run(
            ["X": .float([1, 2], shape: [2]), "Y": .float([1, 2, 3], shape: [3])],
            outputs: ["Z"]
        ))
    }
}
