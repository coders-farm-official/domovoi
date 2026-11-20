import XCTest
@testable import DomovoiMacKit

/// The Mac app's side of the update (MacUpdate.swift), whole, against fakes
/// for the core, the release downloads and the administrator prompt; the
/// downloads are the signed fixtures in mac/tests/release-sig/.
final class MacUpdateTests: XCTestCase {
    let fx = macDir.appendingPathComponent("tests/release-sig")
    let commit = "c0ffee0123456789abcdef0123456789abcdef01"
    var dir: URL!
    var downloads: FakeDownloads!
    var admin: FakeAdmin!
    var server: FakeServer!
    var env: MacStepEnv!

    final class FakeDownloads: ReleaseDownloads {
        var files: [String: URL] = [:]
        var missing: Set<String> = []
        var failing: [String: String] = [:]
        var fetched: [String] = []
        var repos: Set<String> = []
        func fetch(repo: String, tag: String, asset: String, max: Int64, to: URL) -> Fetched {
            fetched.append(asset)
            repos.insert(repo)
            if missing.contains(asset) { return .notFound }
            if let w = failing[asset] { return .failed(w) }
            guard let src = files[asset] else { return .notFound }
            try? FileManager.default.copyItem(at: src, to: to)
            return .ok
        }
    }

    /// The prompt: says yes (or no), then plays the helper, writing the
    /// result file the real one would.
    final class FakeAdmin: AdminPrompt {
        var calls: [(String, [String])] = []
        var answer: AdminOutcome = .exited(0, "")
        var result: MacRunResult? = MacRunResult(ok: true, version: "1.2.0")
        var adminRoot: URL!
        func run(_ program: String, _ args: [String], prompt: String) -> AdminOutcome {
            calls.append((program, args))
            if case .exited = answer, let r = result, let i = args.firstIndex(of: "--run") {
                let d = adminRoot.appendingPathComponent(args[i + 1])
                try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
                try? r.data().write(to: d.appendingPathComponent(MacUpdateRun.resultFile))
            }
            return answer
        }
    }

    final class FakeServer: MacUpdateServer {
        var s: MacUpdateState?
        var claims: [String] = []
        var claimAnswer: String? = "c" + String(repeating: "1", count: 31)
        var reports: [(String?, String?, String, String?, String?, String?)] = []
        var reachable = true
        var reads = 0
        func state() -> MacUpdateState? { reads += 1; return s }
        func claim(_ requestID: String) -> String? { claims.append(requestID); return claimAnswer }
        func report(requestID: String?, claim: String?, outcome: String, detail: String?, log: String?, version: String?) -> Bool {
            guard reachable else { return false }
            reports.append((requestID, claim, outcome, detail, log, version))
            return true
        }
    }

    override func setUpWithError() throws {
        dir = try tempDir()
        downloads = FakeDownloads()
        downloads.files = [MacReleaseNames.package: fx.appendingPathComponent("package.bin"),
                           MacReleaseNames.packageSig: fx.appendingPathComponent("package.bin.sig"),
                           MacReleaseNames.manifest: fx.appendingPathComponent("manifest-1.2.0.json"),
                           MacReleaseNames.manifestSig: fx.appendingPathComponent("manifest-1.2.0.json.sig")]
        admin = FakeAdmin()
        admin.adminRoot = dir.appendingPathComponent("admin")
        server = FakeServer()
        env = MacStepEnv(downloads: downloads, admin: admin, updatesRoot: dir.appendingPathComponent("user/updates"))
        env.adminRoot = admin.adminRoot
        env.key = SSHKey.parseEd25519(try String(contentsOf: fx.appendingPathComponent("test-key.pub"), encoding: .utf8))
        env.installed = "1.1.0"
        env.helper = "/Library/Application Support/Domovoi/bin/DomovoiHelper"
        env.uid = 501
        env.now = { Date(timeIntervalSince1970: 1_800_000_000) }
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    func state(step: Bool = true, request: String? = nil, onThisMac: Bool = false, afterLinux: Bool = false) -> MacUpdateState {
        var s = MacUpdateState()
        s.macVersion = "1.1.0"
        s.targetTag = "v1.2.0"
        s.targetVersion = "1.2.0"
        s.targetCommit = commit
        s.stepNeeded = step
        s.whyNot = step ? nil : "up_to_date"
        s.internet = "always"
        s.requestID = request
        s.requestOnThisMac = onThisMac
        s.requestAfterLinux = afterLinux
        return s
    }

    func outcome(_ p: MacStep.Prepared) -> String? { if case .done(let r) = p { return r.outcome } else { return nil } }

    // ─── the state ─────────────────────────────────────────────────────

    func testTheCoresAnswerDecodes() throws {
        let json = """
        {"schema": 1, "mac": {"version": "1.1.0", "build_commit": "\(commit)", "written_at": "x"},
         "target": {"tag": "v1.2.0", "version": "1.2.0", "commit": "\(commit)"}, "step_needed": true, "why_not": null,
         "internet": "sometimes", "channel": "stable",
         "linux": {"busy": false, "last_update": {"status": "ok", "started_at": "2026-11-02T10:00:00Z", "finished_at": null, "error": null}},
         "request": {"id": "0123456789abcdef", "on_this_pc": true, "after_linux": true, "at": "x", "claimed": false}}
        """
        let s = try XCTUnwrap(MacUpdateState.decode(Data(json.utf8)))
        XCTAssertEqual(s.macVersion, "1.1.0")
        XCTAssertEqual(s.targetTag, "v1.2.0")
        XCTAssertEqual(s.targetCommit, commit)
        XCTAssertTrue(s.stepNeeded)
        XCTAssertEqual(s.lastUpdateStarted, "2026-11-02T10:00:00Z")
        XCTAssertEqual(s.requestID, "0123456789abcdef")
        XCTAssertTrue(s.requestOnThisMac && s.requestAfterLinux && !s.requestClaimed)
        // A tag or commit that isn't one is dropped; a request id that isn't one is no request.
        let odd = json.replacingOccurrences(of: "\"v1.2.0\"", with: "\"v1.2.0; rm\"").replacingOccurrences(of: "0123456789abcdef", with: "x")
        let o = try XCTUnwrap(MacUpdateState.decode(Data(odd.utf8)))
        XCTAssertNil(o.targetTag)
        XCTAssertNil(o.requestID)
        XCTAssertNil(MacUpdateState.decode(Data(#"{"schema": 2}"#.utf8)))
    }

    // ─── before the prompt ─────────────────────────────────────────────

    func testNothingStartsWhenItShouldnt() {
        XCTAssertEqual(outcome(MacStep.prepare(env, nil, linuxFirst: false)), MacOutcome.failed)
        var s = state()
        s.internet = "never"
        XCTAssertEqual(outcome(MacStep.prepare(env, s, linuxFirst: true)), MacOutcome.internetOff)
        s = state()
        s.linuxBusy = true
        XCTAssertEqual(outcome(MacStep.prepare(env, s, linuxFirst: false)), MacOutcome.failed)
        XCTAssertEqual(outcome(MacStep.prepare(env, state(step: false), linuxFirst: false)), MacOutcome.nothingNew)
        env.helper = nil
        XCTAssertEqual(outcome(MacStep.prepare(env, state(), linuxFirst: false)), MacOutcome.notInstalled)
        env.helper = "/h"
        env.key = nil
        XCTAssertEqual(outcome(MacStep.prepare(env, state(), linuxFirst: false)), MacOutcome.noKey)
        XCTAssertTrue(downloads.fetched.isEmpty, "nothing downloaded for any of these")
        XCTAssertTrue(admin.calls.isEmpty)
    }

    func testTheMenusUpdateWithTheMacSideCurrentIsTheLinuxPartAlone() {
        guard case let .launch(run, kind, _, args) = MacStep.prepare(env, state(step: false), linuxFirst: true) else { return XCTFail() }
        XCTAssertEqual(kind, "linux")
        XCTAssertEqual(args, ["update", "--linux-only", "--run", run])
        XCTAssertTrue(downloads.fetched.isEmpty)
    }

    func testAGoodDownloadIsHandedToTheHelperWithItsRunFolder() throws {
        env.releaseRepo = "alex/domovoi-train"
        guard case let .launch(run, kind, version, args) = MacStep.prepare(env, state(), linuxFirst: false) else { return XCTFail() }
        XCTAssertTrue(MacUpdateRun.isRunName(run))
        XCTAssertTrue(run.hasPrefix("20270115-080000-"), run)
        XCTAssertEqual(kind, "package")
        XCTAssertEqual(version, "1.2.0")
        let folder = env.updatesRoot.appendingPathComponent(run)
        XCTAssertEqual(args, ["update", "--run", run, "--from", folder.path, "--tag", "v1.2.0", "--for-uid", "501", "--commit", commit])
        XCTAssertEqual(downloads.fetched, MacReleaseNames.all, "the small files first")
        XCTAssertEqual(downloads.repos, ["alex/domovoi-train"], "from the repository the install names")
        for n in MacReleaseNames.all { XCTAssertTrue(FileManager.default.fileExists(atPath: folder.appendingPathComponent(n).path), n) }
        let perms = try FileManager.default.attributesOfItem(atPath: folder.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(perms?.intValue, 0o700, "this account's alone")
        // The menu's own run (both halves) says so.
        guard case let .launch(_, _, _, both) = MacStep.prepare(env, state(), linuxFirst: true) else { return XCTFail() }
        XCTAssertEqual(both.last, "--linux-first")
    }

    func testARefusedDownloadIsNeverHandedOn() throws {
        // Another key's package signature: deleted, said, and no prompt.
        downloads.files[MacReleaseNames.packageSig] = fx.appendingPathComponent("package.bin.other-key.sig")
        let agent = MacUpdateAgent(server: server, env: env)
        let r = agent.run(state(request: "0123456789abcdef"), fromMenu: false).result
        XCTAssertEqual(r.outcome, MacOutcome.badSignature)
        XCTAssertTrue(r.detail!.hasPrefix("Domovoi didn't install the downloaded package: the package can't be trusted"), r.detail!)
        XCTAssertTrue(r.detail!.hasSuffix("It was deleted."))
        XCTAssertTrue(admin.calls.isEmpty, "never the prompt for a package that doesn't check out")
        let runs = try FileManager.default.contentsOfDirectory(atPath: env.updatesRoot.path)
        XCTAssertEqual(runs.count, 1)
        XCTAssertFalse(FileManager.default.fileExists(atPath: env.updatesRoot.appendingPathComponent(runs[0]).appendingPathComponent(MacReleaseNames.package).path))
        // The claimed request is answered with its claim.
        XCTAssertEqual(server.reports.count, 1)
        XCTAssertEqual(server.reports[0].0, "0123456789abcdef")
        XCTAssertEqual(server.reports[0].1, server.claimAnswer)
        XCTAssertEqual(server.reports[0].2, MacOutcome.badSignature)
    }

    func testAReleaseWithoutTheMacPackageSaysSo() {
        downloads.missing = [MacReleaseNames.manifest]
        XCTAssertEqual(outcome(MacStep.prepare(env, state(), linuxFirst: false)), MacOutcome.noSignedSetup)
        downloads.missing = []
        downloads.failing = [MacReleaseNames.package: "GitHub answered 502"]
        guard case .done(let r) = MacStep.prepare(env, state(), linuxFirst: false) else { return XCTFail() }
        XCTAssertEqual(r.outcome, MacOutcome.downloadFailed)
        XCTAssertTrue(r.detail!.contains("GitHub answered 502") && r.detail!.contains("Nothing was installed"), r.detail!)
    }

    func testNotNewerIsRefusedBeforeThePrompt() {
        env.installed = "1.2.0"
        guard case .done(let r) = MacStep.prepare(env, state(), linuxFirst: false) else { return XCTFail() }
        XCTAssertEqual(r.outcome, MacOutcome.notNewer)
        XCTAssertEqual(r.detail, "Domovoi didn't install the downloaded package: it is version 1.2.0, which isn't newer than the installed 1.2.0.")
    }

    // ─── the prompt and after ──────────────────────────────────────────

    func testTheWholeStepForADashboardRequest() throws {
        let agent = MacUpdateAgent(server: server, env: env)
        let (r, afterLinux) = agent.run(state(request: "0123456789abcdef", onThisMac: true, afterLinux: true), fromMenu: false)
        XCTAssertEqual(r.outcome, MacOutcome.ok)
        XCTAssertEqual(r.version, "1.2.0")
        XCTAssertTrue(afterLinux)
        XCTAssertEqual(server.claims, ["0123456789abcdef"], "claimed before anything starts")
        XCTAssertEqual(admin.calls.count, 1)
        XCTAssertEqual(admin.calls[0].0, env.helper)
        XCTAssertFalse(admin.calls[0].1.contains("--linux-first"), "the dashboard's press did the Linux part already")
        XCTAssertEqual(server.reports.map { $0.2 }, [MacOutcome.ok])
        XCTAssertEqual(server.reports[0].5, "1.2.0")
        XCTAssertNil(MacPendingRun.load(env.updatesRoot), "reported: nothing pending")
        let left = (try? FileManager.default.contentsOfDirectory(atPath: env.updatesRoot.path)) ?? []
        XCTAssertFalse(left.contains(where: MacUpdateRun.isRunName), "the account's download is gone once the helper has its copy: \(left)")
        XCTAssertEqual(MacStep.words(r, afterLinux: true), "Domovoi is updated: the Mac side is now 1.2.0.")
    }

    func testAnotherAccountsAppHasTheRequest() {
        server.claimAnswer = nil
        let r = MacUpdateAgent(server: server, env: env).run(state(request: "0123456789abcdef"), fromMenu: false).result
        XCTAssertEqual(r.outcome, MacOutcome.nothingNew)
        XCTAssertTrue(downloads.fetched.isEmpty && admin.calls.isEmpty && server.reports.isEmpty)
    }

    func testADeclinedPromptIsHalfDoneAfterTheLinuxPart() {
        admin.answer = .declined
        let (r, afterLinux) = MacUpdateAgent(server: server, env: env).run(state(request: "0123456789abcdef", afterLinux: true), fromMenu: false)
        XCTAssertEqual(r.outcome, MacOutcome.declined)
        XCTAssertTrue(MacStep.words(r, afterLinux: afterLinux).contains("half done"))
        XCTAssertEqual(server.reports.map { $0.2 }, [MacOutcome.declined])
    }

    func testWhatTheHelperSaidIsWhatTheCardGets() {
        func finish(_ rr: MacRunResult?, kind: String = "package", code: Int32 = 1) -> MacStepResult {
            let run = "20270115-080000-0a1b2c3d"
            let d = dir.appendingPathComponent("admin2").appendingPathComponent(run)
            try? FileManager.default.removeItem(at: d)
            try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
            if let rr { try? rr.data().write(to: d.appendingPathComponent(MacUpdateRun.resultFile)) }
            return MacStep.finish(adminRoot: dir.appendingPathComponent("admin2"), run: run, exitCode: code, kind: kind, version: "1.2.0")
        }
        XCTAssertEqual(finish(MacRunResult(ok: false, outcome: "bad_signature", error: "Domovoi didn't install the package: x")).outcome, "bad_signature")
        let lf = finish(MacRunResult(ok: false, outcome: "linux_failed", error: "the pull failed", linux: "failed"))
        XCTAssertEqual(lf.outcome, MacOutcome.linuxFailed)
        XCTAssertEqual(lf.detail, "The Linux part didn't update: the pull failed. Nothing on the Mac was changed.")
        let rf = finish(MacRunResult(ok: false, outcome: "repair_failed", error: "the repair after the package failed at provision: venv",
                                     log: "/Library/Logs/Domovoi/helper.log"))
        XCTAssertEqual(rf.outcome, MacOutcome.repairFailed)
        XCTAssertEqual(rf.log, "/Library/Logs/Domovoi/helper.log")
        XCTAssertTrue(rf.detail!.hasPrefix("The package couldn't update the Mac side"))
        let none = finish(nil, code: 3)
        XCTAssertEqual(none.outcome, MacOutcome.repairFailed)
        XCTAssertTrue(none.detail!.contains("exit code 3"))
        XCTAssertEqual(finish(MacRunResult(ok: true, linux: "nothing_new"), kind: "linux", code: 0).outcome, MacOutcome.nothingNew)
        XCTAssertEqual(finish(MacRunResult(ok: true, linux: "updated"), kind: "linux", code: 0).outcome, MacOutcome.ok)
        XCTAssertNil(finish(MacRunResult(ok: true, linux: "updated"), kind: "linux", code: 0).version, "no Mac version for the Linux part")
    }

    // ─── a run the app didn't live to report ───────────────────────────

    func testAnUnreportedRunIsReportedBeforeTheStateIsRead() throws {
        server.reachable = false
        let agent = MacUpdateAgent(server: server, env: env)
        let r = agent.run(state(request: "0123456789abcdef", onThisMac: true), fromMenu: false).result
        XCTAssertEqual(r.outcome, MacOutcome.ok)
        let p = try XCTUnwrap(MacPendingRun.load(env.updatesRoot), "kept until the core has it")
        XCTAssertEqual(p.outcome, MacOutcome.ok)
        XCTAssertEqual(p.claim, server.claimAnswer)
        // While it waits, the same request is never started again.
        server.s = state(request: "0123456789abcdef", onThisMac: true)
        XCTAssertEqual(agent.poll(seenRequest: nil, stepRunning: false).action, .nothing)
        // Back: the outcome goes first, then the state.
        server.reachable = true
        server.s = state(step: false)
        let poll = agent.poll(seenRequest: nil, stepRunning: false)
        XCTAssertEqual(poll.done?.result.outcome, MacOutcome.ok)
        XCTAssertEqual(server.reports.last?.1, server.claimAnswer, "with its claim")
        XCTAssertNil(MacPendingRun.load(env.updatesRoot))
        XCTAssertEqual(poll.action, .nothing)
    }

    func testARunEndedWithTheAppIsFinishedFromItsResultFile() throws {
        // The package replaced the app mid-run: pending.json without an outcome.
        let run = "20270115-075000-0a1b2c3d"
        try MacPendingRun(run: run, requestID: "0123456789abcdef", claim: "k", kind: "package", started: "2027-01-15T07:50:00Z",
                          version: "1.2.0", afterLinux: false).save(env.updatesRoot)
        let agent = MacUpdateAgent(server: server, env: env)
        XCTAssertNil(agent.finishPending(), "no result yet, and not old: still running")
        let d = env.adminRoot.appendingPathComponent(run)
        try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        try MacRunResult(ok: true, version: "1.2.0").data().write(to: d.appendingPathComponent(MacUpdateRun.resultFile))
        let done = try XCTUnwrap(agent.finishPending())
        XCTAssertEqual(done.result.outcome, MacOutcome.ok)
        XCTAssertTrue(done.show, "nobody saw it end")
        XCTAssertEqual(server.reports.map { $0.2 }, [MacOutcome.ok])
    }

    func testARunThatNeverSaidIsAFailureAfterThreeHours() throws {
        try MacPendingRun(run: "20270115-030000-0a1b2c3d", requestID: nil, claim: nil, kind: "package", started: "2027-01-15T03:00:00Z",
                          version: "1.2.0", afterLinux: false).save(env.updatesRoot)
        let done = try XCTUnwrap(MacUpdateAgent(server: server, env: env).finishPending())
        XCTAssertEqual(done.result.outcome, MacOutcome.repairFailed)
    }

    // ─── what a poll does ──────────────────────────────────────────────

    func testThePollsDecision() {
        let d = MacUpdateDecision.onState
        XCTAssertEqual(d(state(request: "0123456789abcdef", onThisMac: true), nil, false, false), .startNow)
        XCTAssertEqual(d(state(request: "0123456789abcdef"), nil, false, false), .notify)
        XCTAssertEqual(d(state(request: "0123456789abcdef"), "0123456789abcdef", false, false), .nothing, "seen already")
        XCTAssertEqual(d(state(request: "0123456789abcdef"), nil, true, false), .nothing, "a step is running")
        XCTAssertEqual(d(state(request: "0123456789abcdef"), nil, false, true), .nothing, "an outcome waits to be reported")
        XCTAssertEqual(d(state(step: false, request: "0123456789abcdef"), nil, false, false), .nothing)
        var claimed = state(request: "0123456789abcdef")
        claimed.requestClaimed = true
        XCTAssertEqual(d(claimed, nil, false, false), .nothing, "another account's app has it")
        XCTAssertEqual(d(state(), nil, false, false), .nothing, "no request")
        XCTAssertEqual(d(nil, nil, false, false), .nothing)
    }

    // ─── the real prompt's command line ────────────────────────────────

    func testTheAdministratorPromptsShellLine() throws {
        let line = try OsascriptAdmin.shellLine("/Library/Application Support/Domovoi/bin/DomovoiHelper", ["update", "--run", "r"])
        XCTAssertTrue(line.hasPrefix("out=$('/Library/Application Support/Domovoi/bin/DomovoiHelper' 'update' '--run' 'r' </dev/null 2>&1); s=$?;"), line)
        XCTAssertThrowsError(try OsascriptAdmin.shellLine("/h", ["it's"]))
        let script = OsascriptAdmin.appleScript(line, prompt: MacStep.prompt)
        XCTAssertTrue(script.hasPrefix("do shell script \"out=$("))
        XCTAssertTrue(script.hasSuffix("with administrator privileges without altering line endings"))
        XCTAssertTrue(script.contains("printf '@@domovoi-exit:%s\\\\n' \\\"$s\\\""), script)
        XCTAssertEqual(OsascriptAdmin.parse("@@domovoi-exit:1\nlast line\n")?.0, 1)
        XCTAssertEqual(OsascriptAdmin.parse("@@domovoi-exit:0\n")?.1, "")
        XCTAssertNil(OsascriptAdmin.parse("nothing\n"))
    }

    func testTheAppsArgvParsesInTheHelper() throws {
        // The P3-5/P3-7 lesson: what the app sends, through the parser the
        // helper runs.
        let from = URL(fileURLWithPath: "/Users/alex/Library/Application Support/Domovoi/updates/20270115-080000-0a1b2c3d")
        var a = try Args(MacUpdateRun.packageArguments(from: from, run: "20270115-080000-0a1b2c3d", tag: "v1.2.0", commit: commit, linuxFirst: true, forUID: 501))
        XCTAssertEqual(a.command, "update")
        let r = try HelperUpdateRequest.parse(&a)
        XCTAssertEqual(r.from?.path, from.path)
        XCTAssertEqual(r.tag, "v1.2.0")
        XCTAssertEqual(r.commit, commit)
        XCTAssertEqual(r.forUID, 501)
        XCTAssertTrue(r.linuxFirst && !r.linuxOnly)
        var l = try Args(MacUpdateRun.linuxArguments(run: "20270115-080000-0a1b2c3d"))
        XCTAssertTrue(try HelperUpdateRequest.parse(&l).linuxOnly)
        for bad in [["update", "--run", "x"], ["update", "--run", "20270115-080000-0a1b2c3d", "--from", "rel", "--tag", "v1.2.0", "--for-uid", "501"],
                    ["update", "--run", "20270115-080000-0a1b2c3d", "--from", "/x", "--tag", "1.2.0", "--for-uid", "501"],
                    ["update", "--run", "20270115-080000-0a1b2c3d", "--from", "/x", "--tag", "v1.2.0", "--for-uid", "0"],
                    ["update", "--linux-only", "--run", "20270115-080000-0a1b2c3d", "--tag", "v1.2.0"],
                    ["update", "--run", "20270115-080000-0a1b2c3d", "--from", "/x", "--tag", "v1.2.0", "--for-uid", "501", "--bogus", "1"]] {
            var b = try Args(bad)
            XCTAssertThrowsError(try HelperUpdateRequest.parse(&b), "\(bad)")
        }
    }
}
