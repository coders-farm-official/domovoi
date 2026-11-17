import XCTest
import CoreGraphics
@testable import DomovoiMacKit

/// The secrets rule of every log copy: the Windows side's cases
/// (windows/tests/Tests.cs, LogCopies), the same answers.
final class RedactTests: XCTestCase {
    func testNoSetupCodeNoPasswordInAURL() {
        XCTAssertEqual(Redact.line("code acorn-apple-arrow-autumn-badge-baker-basil-beach here"), "code [hidden] here")
        XCTAssertEqual(Redact.line("postgresql://domovoi:s3cret-Pass@127.0.0.1:6432/domovoi"), "postgresql://domovoi:[hidden]@127.0.0.1:6432/domovoi")
        XCTAssertEqual(Redact.line("https://github.com/coders-farm-official/domovoi"), "https://github.com/coders-farm-official/domovoi")
        let plain = "installed /etc/sudoers.d/domovoi-update: domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl"
        XCTAssertEqual(Redact.line(plain), plain)
    }

    func testKeysHeadersAndTheOtherURLForms() {
        let cases: [(String, String)] = [
            ("password=hunter2 next", "password=[hidden] next"),
            ("POSTGRES_PASSWORD=Xy7%q", "POSTGRES_PASSWORD=[hidden]"),
            ("LETTA_TOKEN = abc123", "LETTA_TOKEN = [hidden]"),
            ("token=abc rest", "token=[hidden] rest"),
            ("secret: s3cr3t", "secret: [hidden]"),
            (#"{"api_key": "k-123", "n": 1}"#, #"{"api_key": "[hidden]", "n": 1}"#),
            ("Authorization: Bearer eyJhbGciOi.abc.def", "Authorization: Bearer [hidden]"),
            ("Authorization: Basic ZG9tb3ZvaTpwdw==", "Authorization: Basic [hidden]"),
            ("sent bearer abcdefghijkl to x", "sent bearer [hidden] to x"),
            ("postgresql://:pw@127.0.0.1/db", "postgresql://:[hidden]@127.0.0.1/db"),
            ("https://ghp_abc123@github.com/x", "https://[hidden]@github.com/x"),
            ("postgresql://domovoi:pa/ss@h/db", "postgresql://domovoi:[hidden]@h/db"),
        ]
        for (input, want) in cases { XCTAssertEqual(Redact.line(input), want, input) }
        for keep in [
            "provision: created the service user domovoi (password login locked)",
            "Collecting tokenizers==0.23.2 (from -r requirements-linux-py314.lock (line 2816))",
            "Please set a HF_TOKEN to enable higher rate limits",
            "/opt/domovoi/domovoi/.env: LETTA_TOKEN and SEARXNG_SECRET already set; left untouched",
            "Downloading https://download-r2.pytorch.org/whl/cpu/torch-2.14.1%2Bcpu-cp314-cp314-manylinux_2_28_aarch64.whl (196.3 MB)",
            "authorization failed for the request",
        ] {
            XCTAssertEqual(Redact.line(keep), keep, "kept: \(keep)")
        }
    }

    func testAWholeTextKeepsItsLines() {
        XCTAssertEqual(Redact.text("a\npassword=x\n\nb"), "a\npassword=[hidden]\n\nb")
    }
}

/// What the icon says: the Windows tray's cases (windows/tests/TrayTests.cs,
/// TrayStatus), then the Mac's own: a service switched off in Login Items,
/// a missing one, not set up, uninstalled with the data kept.
final class MenuStatusTests: XCTestCase {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    func decide(_ f: MenuFacts, since: Date) -> MenuVerdict { MenuStatus.decide(f, notReadySince: since, now: now) }

    func facts(_ change: (inout MenuFacts) -> Void) -> MenuFacts {
        var f = MenuFacts()
        f.install = .installed
        f.vm = .running
        f.ollama = .running
        change(&f)
        return f
    }

    func testWhatTheIconSays() {
        XCTAssertEqual(decide(facts { $0.web = .ok }, since: now).state, .running)
        XCTAssertEqual(decide(facts { $0.web = .ok }, since: now).line, "Domovoi is running")
        XCTAssertEqual(decide(facts { $0.vm = .running }, since: now.addingTimeInterval(-9 * 60)).state, .starting, "inside the grace")
        let late = decide(facts { _ in }, since: now.addingTimeInterval(-11 * 60))
        XCTAssertEqual(late.state, .problem, "past the grace")
        XCTAssertEqual(late.detail, "Nothing is answering.")
        XCTAssertEqual(decide(facts { $0.web = .degraded; $0.dbReachable = true; $0.coreAlive = false }, since: now.addingTimeInterval(-3600)).detail,
                       "The voice service isn't running.")
        XCTAssertEqual(decide(facts { $0.web = .degraded; $0.dbReachable = false; $0.coreAlive = true }, since: now.addingTimeInterval(-3600)).detail,
                       "Its database isn't answering.")
        XCTAssertEqual(decide(facts { $0.coreAlive = true }, since: now.addingTimeInterval(-3600)).detail, "The dashboard isn't answering.")
        // A job that doesn't run: starting at first, then stopped.
        XCTAssertEqual(decide(facts { $0.vm = .notRunning }, since: now).state, .starting)
        let stopped = decide(facts { $0.vm = .notRunning }, since: now.addingTimeInterval(-3600))
        XCTAssertEqual(stopped.state, .stopped)
        XCTAssertEqual(stopped.line, "Domovoi is stopped")
        XCTAssertEqual(stopped.notice, "Rooms and phones can't reach it until it starts again.")
    }

    func testABackgroundItemSwitchedOffIsSaid() {
        // PLAN R8: the owner switched "limactl" off in Login Items.
        let v = decide(facts { $0.vm = .off }, since: now)
        XCTAssertEqual(v.state, .stopped, "at once, not after the grace")
        XCTAssertEqual(v.line, "Domovoi is switched off")
        XCTAssertTrue(v.notice!.contains("“limactl”") && v.notice!.contains("Login Items & Extensions"), v.notice!)
        let a = MenuStatus.attention(facts { $0.vm = .off })
        XCTAssertEqual(a.map(\.kind), [.vmSwitchedOff])
        XCTAssertTrue(a[0].opensLoginItems)
        // Ollama switched off: Domovoi answers, without its AI.
        let o = facts { $0.web = .ok; $0.ollama = .off }
        XCTAssertEqual(decide(o, since: now).state, .running)
        XCTAssertEqual(MenuStatus.attention(o).map(\.kind), [.ollamaSwitchedOff])
        XCTAssertTrue(MenuStatus.attention(o)[0].text.contains("Infra Technologies"))
        // The household's own Ollama is never ours to report.
        XCTAssertEqual(MenuStatus.attention(facts { $0.web = .ok; $0.ownOllama = false; $0.ollama = .off }), [])
        XCTAssertEqual(MenuStatus.attention(facts { $0.web = .ok; $0.ollamaAnswers = false }).map(\.kind), [.aiNotAnswering])
    }

    func testNotSetUpMissingAndKeptData() {
        var f = MenuFacts()
        XCTAssertEqual(decide(f, since: now).state, .notInstalled)
        XCTAssertEqual(decide(f, since: now).line, "Domovoi isn't set up on this Mac")
        f.install = .dataKept
        XCTAssertEqual(decide(f, since: now).line, "Domovoi is uninstalled")
        XCTAssertEqual(MenuStatus.attention(f), [])
        XCTAssertEqual(decide(facts { $0.vm = .missing }, since: now).line, "Domovoi needs a repair")
        XCTAssertEqual(decide(facts { $0.vm = .missing }, since: now).state, .problem)
        for vm in [JobFact.running, .notRunning, .off, .missing, .unknown] {
            for web in [WebFact.ok, .degraded, .noAnswer] {
                let v = decide(facts { $0.vm = vm; $0.web = web }, since: now.addingTimeInterval(-3600))
                XCTAssertTrue(!v.line.isEmpty && v.line.count <= 63, v.line)
            }
        }
    }

    func testTheGraceCountsFromTheFirstPollThatSawItUpButNotReady() {
        let clock = ReadyClock()
        var off = facts { _ in }
        off.vm = .off
        XCTAssertEqual(clock.since(off, now: now), now, "switched off: no clock")
        XCTAssertEqual(clock.since(facts { _ in }, now: now.addingTimeInterval(60)), now.addingTimeInterval(60), "starts")
        XCTAssertEqual(clock.since(facts { _ in }, now: now.addingTimeInterval(300)), now.addingTimeInterval(60), "keeps its start")
        _ = clock.since(facts { $0.web = .ok }, now: now.addingTimeInterval(360))
        XCTAssertEqual(clock.since(facts { _ in }, now: now.addingTimeInterval(420)), now.addingTimeInterval(420), "restarts after a healthy poll")
        XCTAssertEqual(clock.since(MenuFacts(), now: now.addingTimeInterval(500)), now.addingTimeInterval(500), "not set up: no clock")
    }

    func testNotificationsOnlyWhenDomovoiGoesDownOrComesBack() {
        let n = StatusNotifier()
        func next(_ s: MenuState) -> (title: String, body: String)? {
            n.next(MenuVerdict(state: s, line: s == .stopped ? "Domovoi is stopped" : "x", detail: "d", notice: s == .stopped ? "gone" : nil))
        }
        XCTAssertNil(next(.running), "not at start")
        XCTAssertNil(next(.running), "not every poll")
        XCTAssertNil(next(.starting), "not while restarting")
        XCTAssertNil(next(.running), "an update that came back: nothing")
        XCTAssertEqual(next(.stopped)?.title, "Domovoi is stopped")
        XCTAssertNil(next(.problem), "still down")
        XCTAssertEqual(next(.running)?.title, "Domovoi is running again")
        let m = StatusNotifier()
        XCTAssertNil(m.next(MenuVerdict(state: .problem, line: "Domovoi has a problem")), "an icon that starts on a problem says nothing yet")
        XCTAssertNil(m.next(MenuVerdict(state: .notInstalled, line: "x")), "nothing to watch")
    }

    func testAnAttentionItemIsNotifiedOnceUntilItComesBack() {
        let n = AttentionNotifier()
        let off = MenuStatus.attention(facts { $0.web = .ok; $0.ollama = .off })
        XCTAssertEqual(n.next(off).count, 1, "said at once, also at the icon's start")
        XCTAssertEqual(n.next(off).count, 0, "once")
        XCTAssertEqual(n.next([]).count, 0)
        XCTAssertEqual(n.next(off).count, 1, "again when it comes back")
        XCTAssertEqual(n.next(MenuStatus.attention(facts { $0.vm = .off })).count, 0, "the status notification says that one")
    }
}

/// The facts, from fakes: the record on disk, HTTP, launchctl and pgrep.
final class MenuProbeTests: XCTestCase {
    var dir: URL!
    var layout: Layout!

    override func setUpWithError() throws {
        dir = try tempDir()
        layout = Layout(mode: .system, root: dir)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    func writeRecord(ollama: String = "own", uninstalled: Bool = false) throws {
        let ans = InstallRecord.Answers(internet: "never", tier: .floor, ollama: ollama, mediaRoot: "/Users/Shared/Domovoi", media: [],
                                        ports: Ports.real, size: VMSize(cpus: 4, memoryGiB: 6, diskGiB: 40), timezone: nil)
        var rec = InstallRecord(mode: .system, root: "/", answers: ans, launchdDomain: "system")
        rec.roleAccount = "_domovoi"
        if uninstalled { rec.uninstalledAt = "2026-11-02T10:00:00Z" }
        try FileManager.default.createDirectory(at: layout.state, withIntermediateDirectories: true)
        try RecordStore(url: layout.installRecord, record: rec, fileOps: LocalFileOps()).save()
        try FileManager.default.createDirectory(at: layout.launchDaemons, withIntermediateDirectories: true)
        for l in [Labels.vm, Labels.ollama] { FileManager.default.createFile(atPath: layout.plist(l).path, contents: Data("x".utf8)) }
    }

    func testHealthyAndSwitchedOff() throws {
        try writeRecord()
        let http = FakeHTTP()
        http.answers["http://127.0.0.1:6369/api/health"] = HTTPResponse(status: 200, body: Data(#"{"status":"ok","db_reachable":true,"domovoi_reachable":true,"core_alive":true}"#.utf8))
        http.answers["http://127.0.0.1:11434/api/version"] = HTTPResponse(status: 200, body: Data(#"{"version":"0.34.4"}"#.utf8))
        let r = FakeRunner()
        r.on { c in
            if c.arguments == ["print-disabled", "system"] {
                return CommandResult(status: 0, stdout: "disabled services = {\n\t\"com.apple.ftpd\" => disabled\n\t\"com.domovoi.mac.ollama\" => disabled\n\t\"com.domovoi.mac.vm\" => enabled\n}\n")
            }
            if c.executable == MenuProbe.pgrep {
                XCTAssertEqual(Array(c.arguments.prefix(3)), ["-x", "-U", "_domovoi"], "the role account's processes only")
                return CommandResult(status: c.arguments.last == "limactl" ? 0 : 1)
            }
            return nil
        }
        let f = MenuProbe(layout: layout, http: http, runner: r).gather()
        XCTAssertEqual(f.install, .installed)
        XCTAssertEqual(f.web, .ok)
        XCTAssertEqual(f.coreAlive, true)
        XCTAssertEqual(f.vm, .running)
        XCTAssertEqual(f.ollama, .off)
        XCTAssertEqual(f.ollamaAnswers, true)
        XCTAssertFalse(r.calls.contains { $0.executable == Lima.sudo }, "no sudo, no root")
    }

    func testNothingAnsweringAndAMissingJob() throws {
        try writeRecord()
        try FileManager.default.removeItem(at: layout.plist(Labels.vm))
        let http = FakeHTTP()
        let r = FakeRunner()
        r.on { c in c.executable == MenuProbe.pgrep ? CommandResult(status: 1) : CommandResult(status: 0, stdout: "disabled services = {\n}\n") }
        let f = MenuProbe(layout: layout, http: http, runner: r).gather()
        XCTAssertEqual(f.web, .noAnswer)
        XCTAssertEqual(f.coreAlive, false)
        XCTAssertEqual(f.vm, .missing)
        XCTAssertEqual(f.ollama, .notRunning)
        XCTAssertNil(f.ollamaAnswers, "not asked while the dashboard is down")
        XCTAssertTrue(http.requested.contains("http://127.0.0.1:6370/v1/alive"))
    }

    func testADashboardThatAnswersBadlyIsDegraded() throws {
        try writeRecord(ollama: "existing:11434")
        let http = FakeHTTP()
        http.answers["http://127.0.0.1:6369/api/health"] = HTTPResponse(status: 503, body: Data("not json".utf8))
        let f = MenuProbe(layout: layout, http: http, runner: FakeRunner()).gather()
        XCTAssertEqual(f.web, .degraded)
        XCTAssertFalse(f.ownOllama)
        XCTAssertEqual(f.ollama, .unknown, "the household's Ollama isn't looked at")
    }

    func testNoRecordAndKeptData() throws {
        let r = FakeRunner()
        XCTAssertEqual(MenuProbe(layout: layout, http: FakeHTTP(), runner: r).gather().install, .notSetUp)
        XCTAssertTrue(r.calls.isEmpty, "nothing to look at")
        try writeRecord(uninstalled: true)
        XCTAssertEqual(MenuProbe(layout: layout, http: FakeHTTP(), runner: r).gather().install, .dataKept)
    }

    func testPrintDisabledBothSpellings() {
        XCTAssertEqual(MenuProbe.parseDisabled("\"a\" => disabled\n\"b\" => enabled\n\"c\" => true\n\"d\" => false\n"), ["a", "c"])
    }
}

/// The menu, as entries: what is there, when, and what never is.
final class MenuItemsTests: XCTestCase {
    let running = MenuVerdict(state: .running, line: "Domovoi is running")

    func titles(_ m: [MenuEntry]) -> [String] { m.map { $0.kind == .separator ? "-" : $0.title } }

    func testTheInstalledMenu() {
        let k = KeepAwakeItem.decide(acSleepMinutes: 0, ours: true, laptop: false)
        let m = MenuItems.build(verdict: running, attention: [], keepAwake: k, update: nil)
        XCTAssertEqual(titles(m), ["Domovoi is running", "-", "Open Domovoi", "User manual", "-", "Keep this Mac awake",
                                   "This Mac doesn't go to sleep by itself while it's plugged in.", "-", "Save logs for support...",
                                   "Restart Domovoi...", "Repair or Uninstall Domovoi...", "-", "Hide this icon"])
        XCTAssertTrue(m.first { $0.action == .keepAwake }!.checked)
        XCTAssertFalse(titles(m).contains { $0.hasPrefix("Update") }, "no update item without Phase 6's offer")
        XCTAssertFalse(titles(m).contains { $0.contains("this network") }, "no LAN address before other devices can reach it")
    }

    func testAttentionUpdateLanAndBusy() {
        let a = MenuStatus.attention({ var f = MenuFacts(); f.install = .installed; f.vm = .off; return f }())
        let m = MenuItems.build(verdict: MenuVerdict(state: .stopped, line: "Domovoi is switched off", detail: "d"), attention: a, keepAwake: nil,
                                update: UpdateOffer(title: "Update Domovoi...", bold: true), lanURLs: ["http://192.168.1.20:6369"], busy: [.restart])
        let t = titles(m)
        XCTAssertTrue(t.contains("Open Login Items Settings..."))
        XCTAssertTrue(t.contains("Update Domovoi..."))
        XCTAssertTrue(t.contains("Open on this network (192.168.1.20)"))
        XCTAssertEqual(m.first { $0.action == .copyAddress }?.payload, "http://192.168.1.20:6369")
        XCTAssertTrue(t.contains("Restarting Domovoi..."))
        XCTAssertFalse(m.first { $0.action == .restart }!.enabled)
        XCTAssertFalse(t.contains("Keep this Mac awake"), "no item when the setting can't be read")
    }

    func testNotSetUpOffersSetupOnly() {
        let m = MenuItems.build(verdict: MenuVerdict(state: .notInstalled, line: "Domovoi isn't set up on this Mac", detail: "Open..."),
                                attention: [], keepAwake: nil, update: nil)
        XCTAssertEqual(m.compactMap(\.action), [.openSetup, .openSetup, .hideIcon])
        XCTAssertFalse(m.contains { $0.action == .restart || $0.action == .saveLogs })
    }

    func testKeepAwakeItemStates() {
        XCTAssertNil(KeepAwakeItem.decide(acSleepMinutes: nil, ours: false, laptop: false))
        let theirs = KeepAwakeItem.decide(acSleepMinutes: 0, ours: false, laptop: false)!
        XCTAssertTrue(theirs.checked)
        XCTAssertFalse(theirs.enabled, "the owner's own setting: shown, not ours to undo")
        XCTAssertEqual(KeepAwakeItem.decide(acSleepMinutes: 0, ours: true, laptop: true)?.line, "A MacBook still sleeps when its lid is closed or on battery.")
        let off = KeepAwakeItem.decide(acSleepMinutes: 10, ours: false, laptop: false)!
        XCTAssertFalse(off.checked)
        XCTAssertTrue(off.enabled)
        XCTAssertEqual(off.line, "This Mac sleeps after 10 minutes idle, and Domovoi stops while it sleeps.")
        XCTAssertEqual(KeepAwakeItem.minutes(1), "1 minute")
        XCTAssertEqual(KeepAwakeItem.minutes(60), "1 hour")
        XCTAssertEqual(KeepAwakeItem.minutes(180), "3 hours")
    }
}

/// Train 1 finding 3: the menu-bar icon is the dashboard's own mascot, not
/// a generic symbol, and stays the same drawing as the SVG.
final class MascotTests: XCTestCase {
    func testThePathsAreTheDashboardMascots() throws {
        let svg = try String(contentsOf: repoDir.appendingPathComponent("web/static/assets/domovoi.svg"), encoding: .utf8)
        let ds = matches(#" d="([^"]+)""#, in: svg)
        XCTAssertEqual(ds, Mascot.paths)
        let circles = matches(#"<circle cx="([0-9.]+)" cy="([0-9.]+)" r="([0-9.]+)""#, in: svg, groups: 3)
        XCTAssertEqual(circles.count, Mascot.eyes.count * 3)
        for (k, e) in Mascot.eyes.enumerated() {
            XCTAssertEqual(Double(circles[k * 3]), Double(e.0))
            XCTAssertEqual(Double(circles[k * 3 + 1]), Double(e.1))
            XCTAssertEqual(Double(circles[k * 3 + 2]), Double(e.2))
        }
        XCTAssertTrue(svg.contains(#"stroke-width="1.4""#))
    }

    func testEveryPathParsesInsideTheBox() throws {
        var all = CGRect.null
        for d in Mascot.paths {
            let p = try XCTUnwrap(Mascot.path(d), d)
            all = all.union(p.boundingBoxOfPath)
        }
        XCTAssertEqual(all.minX, Mascot.bounds.minX, accuracy: 0.01)
        XCTAssertEqual(all.minY, Mascot.bounds.minY, accuracy: 0.01)
        XCTAssertEqual(all.maxX, Mascot.bounds.maxX, accuracy: 0.01)
        XCTAssertEqual(all.maxY, Mascot.bounds.maxY, accuracy: 0.01)
        XCTAssertNil(Mascot.path("M1 2 Q3 4 5 6"), "a command the file doesn't use is refused, not guessed")
    }

    func testItDrawsIntoABitmap() throws {
        let ctx = try XCTUnwrap(CGContext(data: nil, width: 32, height: 32, bitsPerComponent: 8, bytesPerRow: 0,
                                          space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        Mascot.draw(in: ctx, rect: CGRect(x: 0, y: 0, width: 32, height: 32), color: CGColor(red: 0, green: 0, blue: 0, alpha: 1))
        let data = try XCTUnwrap(ctx.data).assumingMemoryBound(to: UInt8.self)
        var inked = 0
        for k in 0..<(32 * 32) where data[k * 4 + 3] > 128 { inked += 1 }   // solid ink, not antialiasing
        XCTAssertGreaterThan(inked, 60)
        XCTAssertLessThan(inked, 32 * 32 / 2, "line art, not a filled blob")
    }

    private func matches(_ pattern: String, in s: String, groups: Int = 1) -> [String] {
        let re = try! NSRegularExpression(pattern: pattern)
        var out: [String] = []
        for m in re.matches(in: s, range: NSRange(s.startIndex..., in: s)) {
            for g in 1...groups { out.append(String(s[Range(m.range(at: g), in: s)!])) }
        }
        return out
    }
}
