import XCTest
@testable import DomovoiMacKit

/// Sitting 3, S3-8 / S3-10: the last page puts the result first and the
/// line to paste in its own box.
final class RemovedPageTests: XCTestCase {
    let command = RoleAccount.finishCommand("_domovoi")

    func everythingOutput() -> String {
        var r = UninstallReport()
        r.recordRemoved = true
        r.unfinished = [AccountRemoval.leftToDoByHand(name: "_domovoi", records: [], stillRunning: [])]
        return "Keep this Mac awake is off: this Mac already stayed awake while plugged in before Domovoi, so it is left as it was.\n"
            + r.summary(removeEverything: true)
    }

    func testTheResultComesFirstAndTheMacSettingsLast() {
        let p = RemovedPage.parse(everythingOutput())
        XCTAssertEqual(p.result, ["Domovoi removed, except for one thing."])
        XCTAssertEqual(p.macSettings, ["Keep this Mac awake is off: this Mac already stayed awake while plugged in before Domovoi, so it is left as it was."])
    }

    func testTheLineToPasteIsSetApart() throws {
        let p = RemovedPage.parse(everythingOutput())
        let item = try XCTUnwrap(p.toDo.first)
        XCTAssertEqual(p.toDo.count, 1)
        XCTAssertEqual(item.command, command)
        XCTAssertTrue(item.text.hasPrefix("macOS did not let Domovoi delete its hidden account _domovoi."), item.text)
        XCTAssertFalse(item.text.contains("sudo"), "the command is not in the text")
        XCTAssertTrue(item.after?.hasPrefix("If Terminal answers \"eDSPermissionError\"") == true, item.after ?? "nil")
    }

    func testKeepDataHasNothingToDo() {
        var r = UninstallReport()
        r.keptAccount = "_domovoi"
        let p = RemovedPage.parse(r.summary(removeEverything: false))
        XCTAssertTrue(p.toDo.isEmpty)
        XCTAssertEqual(p.result.count, 1)
        XCTAssertTrue(p.result[0].hasPrefix("Domovoi removed; its data is kept"))
        XCTAssertTrue(p.macSettings.isEmpty)
    }

    func testOutputWithoutAResultIsShownAsItIs() {
        let p = RemovedPage.parse("something unexpected\n\nsecond line\n")
        XCTAssertEqual(p.result, ["something unexpected", "second line"])
    }

    func testOnlyTheFinishCommandGetsATerminalScript() {
        XCTAssertTrue(RemovedPage.isFinishCommand(command))
        XCTAssertTrue(RemovedPage.isFinishCommand("sudo dscl . -delete /Users/_domovoi"))
        XCTAssertNotNil(RemovedPage.terminalScript(for: command))
        for bad in ["sudo rm -rf /", "sudo dscl . -delete /Users/_domovoi; rm -rf ~",
                    "sudo dscl . -delete /Users/_domovoi && curl x", "sudo dscl . -delete /Users/../x", ""] {
            XCTAssertFalse(RemovedPage.isFinishCommand(bad), bad)
            XCTAssertNil(RemovedPage.terminalScript(for: bad), bad)
        }
    }

    func testTheScriptRunsTheLineAndRemovesItself() throws {
        let s = try XCTUnwrap(RemovedPage.terminalScript(for: command))
        XCTAssertTrue(s.hasPrefix("#!/bin/sh\n"))
        XCTAssertTrue(s.contains("if ( set -e; \(command) ); then"), s)
        XCTAssertTrue(s.contains("rm -f -- \"$0\""))
    }
}
