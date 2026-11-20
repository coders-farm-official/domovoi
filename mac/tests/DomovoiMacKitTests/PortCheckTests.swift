import XCTest
@testable import DomovoiMacKit

/// Sitting 3, S3-9: a port refused only for a lingering connection is not
/// "taken"; anything that listens still is.
final class PortCheckTests: XCTestCase {
    func probe(answers: Bool = false, refused: [Bool], reuseRefused: Bool = false) -> (PortCheck.Probe, () -> Int) {
        var binds = 0
        let p = PortCheck.Probe(
            bindRefused: { _ in
                defer { binds += 1 }
                return binds < refused.count ? refused[binds] : refused.last ?? false
            },
            reuseBindRefused: { _ in reuseRefused },
            answersLocally: { _ in answers })
        return (p, { binds })
    }

    func testAFreePortIsFreeWithNoWait() {
        let (p, _) = probe(refused: [false])
        var waits = 0
        XCTAssertFalse(PortCheck.inUse(6369, probe: p, wait: { _ in waits += 1 }))
        XCTAssertEqual(waits, 0)
    }

    func testSomethingAnsweringLocallyIsTakenAtOnce() {
        let (p, _) = probe(answers: true, refused: [true])
        var waits = 0
        XCTAssertTrue(PortCheck.inUse(6369, probe: p, wait: { _ in waits += 1 }))
        XCTAssertEqual(waits, 0)
    }

    func testAListenerOnEveryAddressIsTakenAtOnce() {
        let (p, _) = probe(refused: [true], reuseRefused: true)
        var waits = 0
        XCTAssertTrue(PortCheck.inUse(6369, probe: p, wait: { _ in waits += 1 }))
        XCTAssertEqual(waits, 0)
    }

    func testALingeringConnectionIsWaitedOutAndThenFree() {
        // Refused twice (TIME_WAIT), then the bind goes through.
        let (p, binds) = probe(refused: [true, true, false])
        var waited: TimeInterval = 0
        XCTAssertFalse(PortCheck.inUse(6369, probe: p, wait: { waited += $0 }))
        XCTAssertEqual(waited, 2 * PortCheck.every)
        XCTAssertEqual(binds(), 3)
    }

    func testARefusalThatNeverClearsCountsAfterTheSettleTime() {
        // A program on one address only looks like a lingering connection:
        // after the settle time it counts as taken, as it always did.
        let (p, _) = probe(refused: [true])
        var waited: TimeInterval = 0
        XCTAssertTrue(PortCheck.inUse(6369, probe: p, wait: { waited += $0 }))
        XCTAssertEqual(waited, PortCheck.settle)
    }

    func testAListenerThatStartsDuringTheWaitIsTaken() {
        var calls = 0
        let p = PortCheck.Probe(bindRefused: { _ in true }, reuseBindRefused: { _ in false },
                                answersLocally: { _ in defer { calls += 1 }; return calls >= 1 })
        XCTAssertTrue(PortCheck.inUse(6369, probe: p, wait: { _ in }))
    }
}

/// S3-9: the sheet names the owner once, as a sentence.
final class PortConflictTextTests: XCTestCase {
    func conflict(_ owner: String, _ ports: [Int]) -> PortConflict {
        PortConflict(owner: owner, ports: ports, program: nil, pid: nil, ollamaVersion: nil, ollamaUsable: false, ollamaIsApp: false)
    }

    func testAnUnseenOwnerIsNamedOnceAndCapitalised() {
        let c = conflict(Preflight.unnamedOwner, [6369])
        XCTAssertEqual(c.sheetTitle, "Another program is using Domovoi's ports")
        XCTAssertEqual(c.sheetText, "Another program holds port 6369, which Domovoi needs. Setup can't see which program it is; it may belong to another account on this Mac. Quit it, then choose Retry.")
        XCTAssertEqual(c.sheetText.components(separatedBy: "another account").count, 2, "said once")
    }

    func testANamedOwnerIsQuitByName() {
        let c = conflict("node", [8051, 8052])
        XCTAssertEqual(c.sheetTitle, "Node is using Domovoi's ports")
        XCTAssertTrue(c.sheetText.hasPrefix("Node holds ports 8051, 8052, which Domovoi needs."), c.sheetText)
        XCTAssertTrue(c.sheetText.hasSuffix("Quit node, then choose Retry."), c.sheetText)
    }
}
