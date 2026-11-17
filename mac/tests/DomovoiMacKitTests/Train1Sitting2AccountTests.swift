import XCTest
@testable import DomovoiMacKit

/// Train 1 S2-20: Remove everything left the `_domovoi` user record (its
/// group went), with a `distnoted` of the account still running.
final class AccountRemovalTests: XCTestCase {
    /// dscl, ps and kill on fakes: the account's two records, and the
    /// processes on this Mac (PID, effective UID, real UID).
    final class World {
        var records: Set<String> = ["/Users/_domovoi", "/Groups/_domovoi"]
        var uid = 394
        var procs: [(pid: Int32, euid: Int, ruid: Int)] = [(614, 394, 394), (700, 501, 501), (1, 0, 0)]
        /// The user record can't go while a process of it runs.
        var userRefusedWhileRunning = true
        /// `dscl -delete /Users/...` answers ok, and the record stays.
        var userStays = false
        /// PIDs that end only on KILL.
        var stubborn: Set<Int32> = []
        var killed: [[String]] = []
        let runner = FakeRunner()
        let dir: URL

        init() throws {
            dir = try tempDir()
            runner.on { [unowned self] c in
                let a = c.arguments
                switch (c.executable as NSString).lastPathComponent {
                case "dscl":
                    let verb = a[1], path = a[2]
                    switch verb {
                    case "-read":
                        guard self.records.contains(path) else { return CommandResult(status: 56, stderr: "eDSRecordNotFound") }
                        return CommandResult(status: 0, stdout: a.count > 3 && a[3] == "UniqueID" ? "UniqueID: \(self.uid)\n" : "RecordName: _domovoi\n")
                    case "-delete":
                        if path.hasPrefix("/Users/") {
                            if self.userRefusedWhileRunning && self.procs.contains(where: { $0.euid == self.uid || $0.ruid == self.uid }) {
                                return CommandResult(status: 40, stderr: "<dscl_cmd> DS Error: -14987 (eDSOperationFailed)")
                            }
                            if self.userStays { return CommandResult(status: 0) }
                        }
                        self.records.remove(path)
                        return CommandResult(status: 0)
                    default:
                        return CommandResult(status: 1)
                    }
                case "ps":
                    XCTAssertEqual(a, RoleAccount.psArguments)
                    return CommandResult(status: 0, stdout: self.procs.map { "  \($0.pid)   \($0.euid)   \($0.ruid)" }.joined(separator: "\n") + "\n")
                case "kill":
                    self.killed.append(a)
                    let pids = Set(a.dropFirst().compactMap { Int32($0) })
                    self.procs.removeAll { pids.contains($0.pid) && (a[0] == "-KILL" || !self.stubborn.contains($0.pid)) }
                    return CommandResult(status: 0)
                default:
                    return nil
                }
            }
        }

        func removal() -> AccountRemoval {
            AccountRemoval(account: RoleAccount(name: "_domovoi", runner: runner), layout: Layout(mode: .system, root: dir), runner: runner,
                           sleeper: FakeSleeper(), fileOps: LocalFileOps())
        }

        func index(_ program: String, _ args: [String]) -> Int? {
            runner.calls.firstIndex { ($0.executable as NSString).lastPathComponent == program && $0.arguments == args }
        }

        deinit { try? FileManager.default.removeItem(at: dir) }
    }

    func testTheAccountsLeftoverProcessIsEndedThenTheUserGoesThenItsGroup() throws {
        let w = try World()
        let removal = w.removal()
        XCTAssertEqual(removal.run(), .deleted(via: "dscl"))
        XCTAssertEqual(w.records, [], "user and group both gone")
        XCTAssertEqual(removal.stillRunning, [])
        XCTAssertEqual(w.killed, [["-TERM", "614"]], "only the account's process, by PID: not 700 (a person's), not 1")
        let domain = try XCTUnwrap(w.index("launchctl", ["bootout", "user/394"]))
        let kill = try XCTUnwrap(w.index("kill", ["-TERM", "614"]))
        let user = try XCTUnwrap(w.index("dscl", [".", "-delete", "/Users/_domovoi"]))
        let group = try XCTUnwrap(w.index("dscl", [".", "-delete", "/Groups/_domovoi"]))
        XCTAssertLessThan(domain, kill, "launchd's domain for the account first")
        XCTAssertLessThan(kill, user, "what runs as it is ended before the delete")
        XCTAssertLessThan(user, group, "the group only after the user")
    }

    func testAProcessThatIgnoresTermGetsKill() throws {
        let w = try World()
        w.procs.append((615, 0, 394))   // root's effective UID, the account's real one
        w.stubborn = [615]
        let removal = w.removal()
        XCTAssertEqual(removal.run(), .deleted(via: "dscl"))
        XCTAssertEqual(w.killed, [["-TERM", "614", "615"], ["-KILL", "615"]])
    }

    func testAUserDeleteThatDidntTakeIsNotCalledDoneAndTheGroupStaysWithIt() throws {
        let w = try World()
        w.userRefusedWhileRunning = false
        w.userStays = true
        let removal = w.removal()
        guard case .refused(let why) = removal.run() else { return XCTFail("a user record still there was called deleted") }
        XCTAssertTrue(why.contains("still there"), why)
        XCTAssertEqual(w.records, ["/Users/_domovoi", "/Groups/_domovoi"], "no half-removed account: the group stays with its user")
        XCTAssertNil(w.index("dscl", [".", "-delete", "/Groups/_domovoi"]))
        let left = try removal.account.remainingRecords()
        let text = AccountRemoval.leftToDoByHand(name: "_domovoi", records: left, stillRunning: removal.stillRunning)
        XCTAssertTrue(text.contains("\n    sudo dscl . -delete /Users/_domovoi; sudo dscl . -delete /Groups/_domovoi\n"), text)
    }

    func testTheLineToPasteNamesOnlyWhatIsLeft() {
        XCTAssertEqual(RoleAccount.finishCommand("_domovoi", records: ["/Users/_domovoi"]), "sudo dscl . -delete /Users/_domovoi")
        XCTAssertEqual(RoleAccount.finishCommand("_domovoi"), "sudo dscl . -delete /Users/_domovoi; sudo dscl . -delete /Groups/_domovoi")
        let left = AccountRemoval.leftToDoByHand(name: "_domovoi", records: ["/Users/_domovoi"], stillRunning: [])
        XCTAssertTrue(left.contains("\n    sudo dscl . -delete /Users/_domovoi\n"), left)
        XCTAssertTrue(left.contains("nothing runs as it any more"), left)
        XCTAssertTrue(UninstallReport.appShowsRemoved(promptOK: false, output: UninstallReport.unfinishedMarker + " " + left))
        let running = AccountRemoval.leftToDoByHand(name: "_domovoi", records: ["/Users/_domovoi"], stillRunning: [614])
        XCTAssertTrue(running.contains("restart this Mac, then open Terminal"), running)
        XCTAssertFalse(running.contains("nothing runs as it"), running)
    }

    func testNeverAProcessOutsideTheRoleAccountsRange() throws {
        let w = try World()
        w.uid = 501   // a record named _domovoi with a person's UID: not ours to end
        w.procs = [(614, 501, 501)]
        w.userRefusedWhileRunning = false
        XCTAssertEqual(w.removal().run(), .deleted(via: "dscl"))
        XCTAssertEqual(w.killed, [])
        XCTAssertFalse(w.runner.calls.contains { ($0.executable as NSString).lastPathComponent == "ps" })
        XCTAssertNil(w.index("launchctl", ["bootout", "user/501"]))
    }

    func testPidsAreReadByUIDOnly() {
        let out = "  614   394   394\n  615     0   394\n  616   394     0\n  700   501   501\n    1   394   394\njunk\n   12   394\n"
        XCTAssertEqual(RoleAccount.pids(owner: 394, psOutput: out), [614, 615, 616])
        XCTAssertEqual(RoleAccount.psArguments, ["-A", "-o", "pid=", "-o", "uid=", "-o", "ruid="])
    }
}
