import XCTest

/// Proves the seam the overnight branches of 2026-11-02 put their cases in:
/// **a test method declared in an extension of ``StubHouseUITests`` in another
/// file is discovered and run by XCTest, and can reach the harness's helpers.**
///
/// Each branch adds `StubHouseUITests+<Item>.swift` with its own cases, so
/// twenty branches never edit the same test file, and a train run's results
/// split back to each branch by the `test<Item>_` prefix.
///
/// Two forms, because which one XCTest finds is the question: one marked
/// `@objc` and one not. Delete this file once the answer is in
/// `STUB-HOUSE.md`.
extension StubHouseUITests {

    @MainActor @objc func testProbe_ExtensionWithObjc() throws {
        // A helper that was `private` until the seam: if this compiles, an
        // extension in another file can reach the harness.
        XCTAssertFalse(Self.ordinaryTypeSize.isEmpty)
    }

    @MainActor func testProbe_ExtensionWithoutObjc() throws {
        XCTAssertFalse(Self.ordinaryTypeSize.isEmpty)
    }
}
