import XCTest
@testable import DomovoiMacKit

final class PinsTests: XCTestCase {
    func testTheRepoPinsParse() throws {
        let p = try Pins.load(macDir.appendingPathComponent("installer/pins.conf"))
        XCTAssertEqual(p.lima.fileName, "lima-\(p.limaVersion)-Darwin-arm64.tar.gz")
        XCTAssertEqual(p.ollama.fileName, "ollama-darwin.tgz")
        XCTAssertEqual(p.ubuntuImage.fileName, "ubuntu-26.04-server-cloudimg-arm64.img")
        XCTAssertGreaterThanOrEqual(OllamaVersion(p.ollamaVersion)!, p.ollamaMinimum)
    }

    func testTheOllamaPinIsTheLinuxOne() throws {
        let p = try Pins.load(macDir.appendingPathComponent("installer/pins.conf"))
        let doc = try String(contentsOf: repoDir.appendingPathComponent("docs/LINUX_HOST.md"), encoding: .utf8)
        XCTAssertTrue(doc.contains("\nOLLAMA_VERSION=\(p.ollamaVersion)\n"), "docs/LINUX_HOST.md pins another Ollama")
    }

    let good = """
    LIMA_VERSION=2.2.1
    LIMA_URL=https://example.org/v2.2.1/lima.tar.gz
    LIMA_SHA256=\(String(repeating: "a", count: 64))
    OLLAMA_VERSION=0.34.4
    OLLAMA_URL=https://example.org/v0.34.4/ollama-darwin.tgz
    OLLAMA_SHA256=\(String(repeating: "b", count: 64))
    OLLAMA_MIN_VERSION=0.12.7
    UBUNTU_IMAGE_URL=https://example.org/img
    UBUNTU_IMAGE_SHA256=\(String(repeating: "c", count: 64))
    """

    func testBadPinsAreRefused() throws {
        XCTAssertNoThrow(try Pins.parse(good))
        for (from, to) in [("LIMA_SHA256=a", "LIMA_SHA256=A"), ("https://example.org/img", "http://example.org/img"),
                           ("OLLAMA_MIN_VERSION=0.12.7", "OLLAMA_MIN_VERSION=0.40.0"), ("/v0.34.4/", "/v0.34.3/"),
                           ("LIMA_VERSION=2.2.1\n", "")] {
            XCTAssertThrowsError(try Pins.parse(good.replacingOccurrences(of: from, with: to)), "\(from) -> \(to)")
        }
        XCTAssertThrowsError(try Pins.parse(good + "\nLIMA_VERSION=2.2.2"), "given twice")
    }
}

final class OllamaTests: XCTestCase {
    func testVersionsReadAsTheCoreReadsThem() {
        XCTAssertEqual(OllamaVersion("0.34.4")?.description, "0.34.4")
        XCTAssertEqual(OllamaVersion("v0.12.7-rc1")?.description, "0.12.7")
        XCTAssertEqual(OllamaVersion("1.2")?.description, "1.2.0")
        XCTAssertEqual(OllamaVersion("1.2.3.4")?.description, "1.2.3")
        XCTAssertNil(OllamaVersion("0.0.0"))
        XCTAssertNil(OllamaVersion("nonsense"))
        XCTAssertNil(OllamaVersion("7"))
        let min = OllamaVersion("0.12.7")!
        XCTAssertTrue(OllamaVersion("0.12.7")! >= min)
        XCTAssertTrue(OllamaVersion("0.12.6")! < min)
        XCTAssertTrue(OllamaVersion("0.9.0")! < min)
        XCTAssertTrue(OllamaVersion("1.0.0")! > min)
    }

    func testChoices() throws {
        XCTAssertEqual(try OllamaChoice.parse("own"), .own)
        XCTAssertEqual(try OllamaChoice.parse("existing"), .existing(port: 11434))
        XCTAssertEqual(try OllamaChoice.parse("existing:11500"), .existing(port: 11500))
        XCTAssertThrowsError(try OllamaChoice.parse("existing:0"))
        XCTAssertThrowsError(try OllamaChoice.parse("theirs"))
    }

    func probe(_ body: String?, status: Int = 200, listener: String? = nil) -> OllamaFound {
        let http = FakeHTTP()
        if let body { http.answers["http://127.0.0.1:11434/api/version"] = HTTPResponse(status: status, body: Data(body.utf8)) }
        let r = FakeRunner()
        r.on { c in
            if c.executable == "/usr/sbin/lsof" {
                // -a: the selectors AND (never every process of the user).
                XCTAssertEqual(Array(c.arguments.prefix(2)), ["-nP", "-a"])
                return listener == nil ? CommandResult(status: 1) : CommandResult(status: 0, stdout: "p4242\n")
            }
            if c.executable == "/bin/ps" { return CommandResult(status: 0, stdout: (listener ?? "") + "\n") }
            return nil
        }
        return OllamaProbe(http: http, runner: r).look(port: 11434)
    }

    let min = OllamaVersion("0.12.7")!

    func testNothingListening() {
        let f = probe(nil)
        XCTAssertFalse(f.answered)
        XCTAssertThrowsError(try OllamaProbe.checkExisting(f, port: 11434, minimum: min)) { XCTAssertTrue("\($0)".contains("no Ollama answers")) }
    }

    func testTooOldIsRefused() {
        let f = probe(#"{"version":"0.12.6"}"#)
        XCTAssertThrowsError(try OllamaProbe.checkExisting(f, port: 11434, minimum: min)) { XCTAssertTrue("\($0)".contains("needs 0.12.7 or newer")) }
    }

    func testNotOllamaIsRefused() {
        XCTAssertThrowsError(try OllamaProbe.checkExisting(probe("<html>", status: 404), port: 11434, minimum: min))
        XCTAssertThrowsError(try OllamaProbe.checkExisting(probe(#"{"version":"banana"}"#), port: 11434, minimum: min))
    }

    func testASourceBuildIsAWarningAsInProvisionSh() throws {
        let w = try OllamaProbe.checkExisting(probe(#"{"version":"0.0.0"}"#), port: 11434, minimum: min)
        XCTAssertTrue(w.contains { $0.contains("0.0.0") })
    }

    func testTheAppIsNamedAsALoginItem() throws {
        let app = probe(#"{"version":"0.40.12"}"#, listener: "/Applications/Ollama.app/Contents/Resources/ollama")
        XCTAssertTrue(app.isTheApp)
        let w = try OllamaProbe.checkExisting(app, port: 11434, minimum: min)
        XCTAssertTrue(w.contains { $0.contains("only while someone is signed in") })
        XCTAssertTrue(w.contains { $0.contains("leaves them there") })

        let service = probe(#"{"version":"0.40.12"}"#, listener: "/opt/homebrew/bin/ollama")
        XCTAssertFalse(service.isTheApp)
        XCTAssertFalse(try OllamaProbe.checkExisting(service, port: 11434, minimum: min).contains { $0.contains("signed in") })
    }
}

/// The speech service's pins and port (mac-installer PHASE8A-STT.md).
final class SttPinsTests: XCTestCase {
    let good = """
    LIMA_VERSION=2.2.1
    LIMA_URL=https://example.org/v2.2.1/lima.tar.gz
    LIMA_SHA256=\(String(repeating: "a", count: 64))
    OLLAMA_VERSION=0.34.4
    OLLAMA_URL=https://example.org/v0.34.4/ollama-darwin.tgz
    OLLAMA_SHA256=\(String(repeating: "b", count: 64))
    OLLAMA_MIN_VERSION=0.12.7
    UBUNTU_IMAGE_URL=https://example.org/img
    UBUNTU_IMAGE_SHA256=\(String(repeating: "c", count: 64))
    """

    let stt = """

    WHISPERCPP_VERSION=1.9.5
    WHISPERCPP_COMMIT=\(String(repeating: "d", count: 40))
    STT_MODEL_NAME=small.en
    STT_MODEL_URL=https://huggingface.co/x/y/resolve/\(String(repeating: "e", count: 40))/ggml-small.en.bin
    STT_MODEL_SHA256=\(String(repeating: "f", count: 64))
    STT_MODEL_BYTES=487614201
    """

    func testTheSpeechPinsAreOptionalAndWholeWhenGiven() throws {
        XCTAssertNil(try Pins.parse(good).stt, "no lines: no speech service")
        let s = try XCTUnwrap(try Pins.parse(good + stt).stt)
        XCTAssertEqual(s.whisperCppVersion, "1.9.5")
        XCTAssertEqual(s.modelName, "small.en")
        XCTAssertEqual(s.model.fileName, "ggml-small.en.bin")
        XCTAssertEqual(s.modelBytes, 487614201)
        for (from, to) in [("WHISPERCPP_COMMIT=d", "WHISPERCPP_COMMIT=D"), ("/resolve/eeee", "/resolve/main/eeee"),
                           ("STT_MODEL_BYTES=487614201", "STT_MODEL_BYTES=x"), ("STT_MODEL_NAME=small.en\n", ""),
                           ("https://huggingface", "http://huggingface"), ("STT_MODEL_NAME=small.en", "STT_MODEL_NAME=a b")] {
            XCTAssertThrowsError(try Pins.parse(good + stt.replacingOccurrences(of: from, with: to)), "\(from) -> \(to)")
        }
    }

    func testTheRepoPinsCarryTheSpeechService() throws {
        let p = try Pins.load(macDir.appendingPathComponent("installer/pins.conf"))
        let s = try XCTUnwrap(p.stt)
        XCTAssertEqual(s.modelName, "small.en")
        XCTAssertEqual(s.model.fileName, "ggml-small.en.bin")
    }

    func testAnOlderRecordWithoutTheSpeechPortStillReads() throws {
        let old = #"{"web":6369,"core":6370,"ollama":11434}"#
        let p = try JSONDecoder().decode(Ports.self, from: Data(old.utf8))
        XCTAssertNil(p.stt)
        XCTAssertEqual(Ports.real.stt, 6371)
        XCTAssertThrowsError(try Ports(web: 6390, core: 6391, ollama: 11500, stt: 6391).check(for: .user), "ports must differ")
        XCTAssertThrowsError(try Ports(web: 6390, core: 6391, ollama: 11500, stt: 6371).check(for: .user), "an installed Domovoi's")
        XCTAssertNoThrow(try Ports(web: 6390, core: 6391, ollama: 11500, stt: 6395).check(for: .user))
    }

    func testProvisionGetsTheServiceOnlyWhenThereIsOne() {
        let src = ProvisionSource(script: URL(fileURLWithPath: "/tmp/provision.sh"), repoURL: "https://example.org/d.git")
        let answers = InstallRecord.Answers(internet: "never", tier: .floor, ollama: "own", mediaRoot: "/Users/Shared/Domovoi", media: [],
                                            ports: Ports.real, size: VMSize(cpus: 4, memoryGiB: 6, diskGiB: 40), timezone: nil)
        let without = ProvisionArgs.build(source: src, answers: answers, ollamaURL: "http://host.lima.internal:11434")
        XCTAssertFalse(without.contains("--host-stt-url"))
        let with = ProvisionArgs.build(source: src, answers: answers, ollamaURL: "http://host.lima.internal:11434",
                                       sttURL: "http://host.lima.internal:6371", sttModel: "small.en")
        XCTAssertNotNil(with.firstRange(of: ["--host-stt-url", "http://host.lima.internal:6371", "--host-stt-model", "small.en",
                                             "--host-stt-shares-gpu"]), "Metal is shared with Ollama: the core is told")
        XCTAssertFalse(without.contains("--host-stt-shares-gpu"), "no speech service on the Mac: nothing shares the chip")
    }

    func testTheSpeechJobRendersForTheRoleAccountOnLoopbackOnly() throws {
        let tpl = try String(contentsOf: macDir.appendingPathComponent("launchd/\(Labels.stt).plist"), encoding: .utf8)
        let p = try LaunchdPlist.render(template: tpl, values: [
            "LABEL": Labels.stt, "STT_BIN": "/Library/Application Support/Domovoi/bin/whisper-server",
            "STT_MODEL": "/Library/Application Support/Domovoi/stt-models/ggml-small.en.bin", "STT_PORT": "6371",
            "JOB_HOME": "/Library/Application Support/Domovoi/home", "LOG_FILE": "/Library/Logs/Domovoi/stt.log",
        ], role: Names.roleAccount)
        XCTAssertEqual(p.label, Labels.stt)
        XCTAssertEqual(p.userName, Names.roleAccount)
        XCTAssertTrue(p.keepAlive)
        let a = p.programArguments
        XCTAssertEqual(a.first, "/Library/Application Support/Domovoi/bin/whisper-server")
        XCTAssertEqual(a[a.firstIndex(of: "--host")! + 1], "127.0.0.1", "never the LAN")
        XCTAssertEqual(a[a.firstIndex(of: "--port")! + 1], "6371")
        XCTAssertFalse(a.contains("--convert"), "never ffmpeg")
        XCTAssertEqual(p.logFiles, ["/Library/Logs/Domovoi/stt.log"])
    }
}
