import Foundation
import XCTest
@testable import DomovoiKit

/// The listening models are taken only from a list our server signed, never
/// older than the last one, only when the app's own pins agree, and only
/// bodies that hash to what the list says; a trained model is re-hashed
/// before every load (Android's `RoomModelsTest`). And the sounds' mirror.
final class RoomModelsTests: XCTestCase {

    private let core = RoomTestCore()

    private struct Served: Sendable {
        var baseList: [String: String] = [:]
        var baseSerial: Int64 = 7
        var trainedList: [String: String] = [:]
        var bodies: [String: Data] = [:]
    }

    private func http(_ served: RoomLock<Served>) -> FakeRoomHTTP {
        let core = self.core
        return FakeRoomHTTP { url in
            let path = String(url.dropFirst("http://192.168.1.30:6370".count))
            let s = served.withLock { $0 }
            switch path {
            case "/v1/wake-base/manifest.sig":
                return Data(core.envelope(channel: RoomIdentity.wakeBaseChannel, manifest: s.baseList, serial: s.baseSerial).utf8)
            case "/v1/wake-models/manifest.sig":
                return Data(core.envelope(channel: RoomIdentity.wakeModelsChannel, manifest: s.trainedList, serial: 3).utf8)
            default:
                return s.bodies[path]
            }
        }
    }

    private let base = "http://192.168.1.30:6370"

    func testAnOlderListThanTheLastAcceptedIsRefusedAndNothingIsFetched() throws {
        let book = InMemoryRoomStore()
        book.remember(RoomIdentity.wakeBaseChannel, RoomSignedList(manifest: [:], serial: 10, issuedAt: 0, digest: "d"))
        let served = RoomLock(Served(baseList: ["melspectrogram.onnx": RoomModels.pins["melspectrogram.onnx"]!.sha], baseSerial: 5))
        let h = http(served)
        let r = RoomModels(directory: RoomTestKit.tempDir(), book: book).sync(http: h, coreURL: base, fingerprint: core.fingerprint, wakeWord: "hey_jarvis")
        guard case .failed(let why) = r, why.contains("older") else { return XCTFail("\(r)") }
        XCTAssertFalse(h.asked.contains { $0.hasSuffix("/v1/wake-base/melspectrogram.onnx") })
        XCTAssertEqual(10, book.seen(RoomIdentity.wakeBaseChannel)?.serial)
    }

    func testAListedDigestTheAppDoesNotPinIsRefused() throws {
        let dir = RoomTestKit.tempDir()
        let served = RoomLock(Served(baseList: ["melspectrogram.onnx": String(repeating: "00", count: 32)],
                                     bodies: ["/v1/wake-base/melspectrogram.onnx": Data(count: 16)]))
        let h = http(served)
        let r = RoomModels(directory: dir, book: InMemoryRoomStore()).sync(http: h, coreURL: base, fingerprint: core.fingerprint, wakeWord: "hey_jarvis")
        guard case .failed(let why) = r, why.contains("does not pin") else { return XCTFail("\(r)") }
        XCTAssertFalse(h.asked.contains { $0.hasSuffix("/v1/wake-base/melspectrogram.onnx") })
        XCTAssertFalse(FileManager.default.fileExists(atPath: dir.appendingPathComponent("melspectrogram.onnx").path))
    }

    func testABodyThatIsNotTheListedFileIsNeverWritten() throws {
        let dir = RoomTestKit.tempDir()
        let pin = RoomModels.pins["melspectrogram.onnx"]!
        let served = RoomLock(Served(baseList: ["melspectrogram.onnx": pin.sha],
                                     bodies: ["/v1/wake-base/melspectrogram.onnx": Data(count: pin.size)]))
        let h = http(served)
        let r = RoomModels(directory: dir, book: InMemoryRoomStore()).sync(http: h, coreURL: base, fingerprint: core.fingerprint, wakeWord: "hey_jarvis")
        guard case .failed(let why) = r, why.contains("not the file") else { return XCTFail("\(r)") }
        XCTAssertTrue(h.asked.contains { $0.hasSuffix("/v1/wake-base/melspectrogram.onnx") })
        let left = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        XCTAssertEqual([], left, "nothing written, no partial file")
    }

    func testAListSignedByAnotherKeyIsRefused() {
        let book = InMemoryRoomStore()
        let h = http(RoomLock(Served()))
        let r = RoomModels(directory: RoomTestKit.tempDir(), book: book)
            .sync(http: h, coreURL: base, fingerprint: "SHA256:X+zrZv/IbzjZUnhsbWlsecLbwjndTpG0ZynXOif7V+k", wakeWord: "hey_jarvis")
        guard case .failed = r else { return XCTFail("\(r)") }
        XCTAssertNil(book.seen(RoomIdentity.wakeBaseChannel))
    }

    func testAnEmptyServerSaysWhichModelsItLacks() {
        let r = RoomModels(directory: RoomTestKit.tempDir(), book: InMemoryRoomStore())
            .sync(http: http(RoomLock(Served())), coreURL: base, fingerprint: core.fingerprint, wakeWord: "hey_jarvis")
        XCTAssertEqual(.missing(["melspectrogram.onnx", "embedding_model.onnx", "silero_vad.onnx", "hey_jarvis_v0.1.onnx"]), r)
    }

    func testATrainedModelIsTakenOnlyAsListedAndReHashedBeforeEveryLoad() throws {
        let book = InMemoryRoomStore()
        let good = Data("trained model body".utf8)
        let served = RoomLock(Served(trainedList: ["hey_house.onnx": RoomModels.sha256(good)],
                                     bodies: ["/v1/wake-models/hey_house.onnx": Data("something else".utf8)]))
        let dir = RoomTestKit.tempDir()
        let m = RoomModels(directory: dir, book: book)
        let bad = m.sync(http: http(served), coreURL: base, fingerprint: core.fingerprint, wakeWord: "hey_house")
        guard case .failed = bad else { return XCTFail("\(bad)") }
        XCTAssertFalse(FileManager.default.fileExists(atPath: dir.appendingPathComponent("hey_house.onnx").path))
        XCTAssertNil(book.trainedSHA("hey_house.onnx"))

        served.withLock { $0.bodies["/v1/wake-models/hey_house.onnx"] = good }
        let ok = m.sync(http: http(served), coreURL: base, fingerprint: core.fingerprint, wakeWord: "hey_house")
        // The base models are not on this server: the room says so.
        guard case .missing = ok else { return XCTFail("\(ok)") }
        XCTAssertTrue(m.trainedModelIntact("hey_house.onnx"))
        try Data("tampered".utf8).write(to: dir.appendingPathComponent("hey_house.onnx"))
        XCTAssertFalse(m.trainedModelIntact("hey_house.onnx"), "a changed file is not loaded")
        XCTAssertNil(m.local("hey_house"), "nothing loads without its base models")
    }

    func testThePinsAreTheCoresPins() throws {
        let text = try String(contentsOf: RoomTestKit.repo.appendingPathComponent("domovoi/satellite_media/fetchers.py"), encoding: .utf8)
        let rule = try NSRegularExpression(pattern: #""([a-z0-9_.]+\.onnx)": \("([0-9a-f]{64})", (\d+)\)"#)
        var found: [String: (String, Int)] = [:]
        for m in rule.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
            guard let name = Range(m.range(at: 1), in: text), let sha = Range(m.range(at: 2), in: text), let size = Range(m.range(at: 3), in: text) else { continue }
            found[String(text[name])] = (String(text[sha]), Int(text[size]) ?? -1)
        }
        XCTAssertEqual(Set(found.keys), Set(RoomModels.pins.keys))
        for (name, pin) in RoomModels.pins {
            XCTAssertEqual(found[name]?.0, pin.sha, name)
            XCTAssertEqual(found[name]?.1, pin.size, name)
        }
    }

    func testSlugsAndFileNames() {
        XCTAssertTrue(RoomModels.isSlug("hey_house"))
        XCTAssertTrue(RoomModels.isSlug("a"))
        XCTAssertFalse(RoomModels.isSlug("_x"))
        XCTAssertFalse(RoomModels.isSlug("Hey"))
        XCTAssertFalse(RoomModels.isSlug("../x"))
        XCTAssertFalse(RoomModels.isSlug(String(repeating: "a", count: 65)))
        XCTAssertFalse(RoomModels.isSlug(nil))
        XCTAssertEqual("hey_jarvis_v0.1.onnx", RoomModels.wakeFileName("hey_jarvis"))
        XCTAssertEqual("hey_house.onnx", RoomModels.wakeFileName("hey_house"))
    }

    // MARK: - sounds

    func testSoundsMirrorTheSignedListAndPruneTheRest() throws {
        let book = InMemoryRoomStore()
        let dir = RoomTestKit.tempDir()
        let clip = Data("greeting".utf8)
        let core = self.core
        let list = ["greetings/greet_a b.mp3": RoomModels.sha256(clip), "../escape.mp3": RoomModels.sha256(clip)]
        let h = FakeRoomHTTP { url in
            if url.hasSuffix("/v1/sounds/manifest.sig?voice=Ryan") {
                return Data(core.envelope(channel: RoomIdentity.soundsChannel, manifest: list, serial: 4).utf8)
            }
            if url.hasSuffix("/v1/sounds/greetings/greet_a%20b.mp3?voice=Ryan") { return clip }
            return nil
        }
        try FileManager.default.createDirectory(at: dir.appendingPathComponent("greetings"), withIntermediateDirectories: true)
        try Data("old".utf8).write(to: dir.appendingPathComponent("greetings/greet_old.mp3"))
        let sounds = RoomSounds(directory: dir, store: book)
        XCTAssertEqual(1, try sounds.sync(http: h, coreURL: base, fingerprint: core.fingerprint, voice: "Ryan"))
        XCTAssertEqual(4, book.seen("satellite-sounds@Ryan")?.serial)
        XCTAssertFalse(FileManager.default.fileExists(atPath: dir.appendingPathComponent("greetings/greet_old.mp3").path), "pruned")
        XCTAssertEqual("greet_a b.mp3", sounds.pickGreeting()?.lastPathComponent)
        XCTAssertNil(sounds.clip("../escape.mp3"))
        XCTAssertNil(sounds.clip("network_issues.mp3"))
        XCTAssertFalse(RoomSounds.safeRel("a/../b"))
        XCTAssertFalse(RoomSounds.safeRel("/a"))
        XCTAssertFalse(RoomSounds.safeRel("a\\b"))
        XCTAssertTrue(RoomSounds.safeRel("greetings/x.mp3"))
        // An older list for the same voice is refused, and the clip stays.
        let older = FakeRoomHTTP { url in
            url.contains("manifest.sig") ? Data(core.envelope(channel: RoomIdentity.soundsChannel, manifest: [:], serial: 2).utf8) : nil
        }
        XCTAssertThrowsError(try sounds.sync(http: older, coreURL: base, fingerprint: core.fingerprint, voice: "Ryan"))
        XCTAssertNotNil(sounds.pickGreeting())
    }
}
