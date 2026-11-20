import XCTest
@testable import DomovoiMacKit

/// The user mode's job runner, with real (harmless) processes: `sleep`
/// and `false`. Each test signals only the pid it started.
final class ProcessJobsTests: XCTestCase {
    var dir: URL!
    var jobs: ProcessJobs!

    override func setUpWithError() throws {
        dir = try tempDir()
        jobs = ProcessJobs(folder: dir, fileOps: LocalFileOps(), sleeper: RealSleeper())
        jobs.settle = 0.5
    }

    override func tearDownWithError() throws {
        try? jobs.bootout("com.domovoi.mac.t", wait: 5)
        try? FileManager.default.removeItem(at: dir)
    }

    func plist(_ program: [String], env: [String: String] = [:]) throws -> LaunchdPlist {
        let args = program.map { "<string>\(Template.xmlEscape($0))</string>" }.joined()
        let envXML = env.map { "<key>\($0.key)</key><string>\(Template.xmlEscape($0.value))</string>" }.joined()
        return try LaunchdPlist(text: """
        <?xml version="1.0" encoding="UTF-8"?>
        <plist version="1.0"><dict><key>Label</key><string>com.domovoi.mac.t</string>
        <key>ProgramArguments</key><array>\(args)</array>
        <key>EnvironmentVariables</key><dict>\(envXML)</dict>
        <key>KeepAlive</key><true/>
        <key>StandardOutPath</key><string>\(dir.path)/t.log</string><key>StandardErrorPath</key><string>\(dir.path)/t.log</string>
        </dict></plist>
        """)
    }

    var plistPath: URL { dir.appendingPathComponent("com.domovoi.mac.t.plist") }

    func testStartsInspectsAndStops() throws {
        XCTAssertNil(try jobs.state("com.domovoi.mac.t"))
        let st = try jobs.install(plist(["/bin/sleep", "60"]), at: plistPath)
        XCTAssertTrue(st.running)
        let pid = try XCTUnwrap(st.pid)
        XCTAssertEqual(ProcessJobs.executable(of: pid), "/bin/sleep")
        XCTAssertEqual(try String(contentsOf: jobs.pidFile("com.domovoi.mac.t"), encoding: .utf8), "\(pid)\n")
        try jobs.bootout("com.domovoi.mac.t", wait: 10)
        XCTAssertNotEqual(kill(pid, 0), 0, "stopped")
        XCTAssertNil(try jobs.state("com.domovoi.mac.t"), "the pid file goes with it")
    }

    func testTheEnvironmentAndLogAreThePlists() throws {
        try jobs.install(plist(["/bin/bash", "-c", "echo \"home=$HOME x=$DOMOVOI_X path=$PATH\"; while :; do /bin/sleep 1; done"], env: ["HOME": "/tmp/jobhome", "DOMOVOI_X": "1"]), at: plistPath)
        let log = try String(contentsOf: dir.appendingPathComponent("t.log"), encoding: .utf8)
        XCTAssertTrue(log.contains("home=/tmp/jobhome x=1 path=/usr/bin:/bin:/usr/sbin:/sbin"), log)
    }

    func testAJobThatExitsAtOnceFailsTheInstall() throws {
        XCTAssertThrowsError(try jobs.install(plist(["/bin/bash", "-c", "echo cannot open the models folder; exit 3"]), at: plistPath)) { e in
            XCTAssertTrue("\(e)".contains("exited at once: cannot open the models folder"), "\(e)")
        }
    }

    func testAPidThatIsntTheJobsProgramIsNeverSignalled() throws {
        // A stale pid file naming another process (here: this test runner).
        try LocalFileOps().writeAtomically(Data(try plist(["/bin/sleep", "60"]).text.utf8), to: plistPath, mode: 0o644)
        try "\(getpid())\n".write(to: jobs.pidFile("com.domovoi.mac.t"), atomically: true, encoding: .utf8)
        XCTAssertEqual(try jobs.state("com.domovoi.mac.t")?.running, false)
        try jobs.bootout("com.domovoi.mac.t", wait: 1)
        XCTAssertEqual(kill(getpid(), 0), 0, "still alive")
    }

    func testTheJobFileMustSitBesideItsPid() throws {
        XCTAssertThrowsError(try jobs.install(plist(["/bin/sleep", "1"]), at: dir.appendingPathComponent("elsewhere.plist")))
    }
}
