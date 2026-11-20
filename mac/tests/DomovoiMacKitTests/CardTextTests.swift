import XCTest
@testable import DomovoiMacKit

/// The Mac card helper's text edits against the server's own output: the
/// goldens windows/tests/card-goldens.json holds (made by
/// domovoi/tests/test_windows_card_parity.py from overlay.write_overlay),
/// the same file the Windows tray's TrayTests.cs checks its port against.
final class CardTextTests: XCTestCase {
    func goldens() throws -> [String: Any] {
        let url = repoDir.appendingPathComponent("windows/tests/card-goldens.json")
        let data = try Data(contentsOf: url)
        return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func b64(_ v: Any?) throws -> [UInt8] {
        let s = try XCTUnwrap(v as? String)
        return [UInt8](try XCTUnwrap(Data(base64Encoded: s)))
    }

    func utf(_ b: [UInt8]) throws -> String { try XCTUnwrap(String(bytes: b, encoding: .utf8)) }

    func zips() throws -> [String: [String: Any]] {
        try XCTUnwrap(try goldens()["zips"] as? [String: [String: Any]])
    }

    func sha(_ b: [UInt8]) -> String { SHA256Hex.of(Data(b)) }

    func testTheGoldensAreThereAndCoverEveryInputAndSetup() throws {
        let g = try goldens()
        XCTAssertGreaterThanOrEqual((g["cases"] as? [Any])?.count ?? 0, 50)
        XCTAssertGreaterThanOrEqual((g["inputs"] as? [Any])?.count ?? 0, 17)
        XCTAssertEqual(try zips().keys.sorted(), ["portal", "portal-usb-mic", "usb"])
    }

    func testTheZipsAdditionsGiveTheBlockAndTheTokens() throws {
        for (name, z) in try zips() {
            let cfg = try utf(b64(z["config"])), cmd = try utf(b64(z["cmdline"]))
            XCTAssertEqual(CardText.blockFromZip(cfg).map(CardText.string), cfg, "\(name) block")
            let tokens = try XCTUnwrap(CardText.tokensFromZip(cmd), "\(name) tokens")
            XCTAssertTrue(tokens.contains(CardText.text(CardText.firstrunToken)), "\(name) hook")
        }
        XCTAssertTrue(try utf(b64(zips()["usb"]?["config"])).contains("dtoverlay=dwc2,dr_mode=peripheral"))
        XCTAssertTrue(try utf(b64(zips()["portal-usb-mic"]?["cmdline"])).contains("dwc_otg.speed=1"))
    }

    func testEveryInputGivesTheServersBytes() throws {
        let g = try goldens()
        let inputs = try XCTUnwrap(g["inputs"] as? [[String: Any]])
        let cases = try XCTUnwrap(g["cases"] as? [[String: Any]])
        let zips = try zips()
        var n = 0
        for c in cases {
            let index = try XCTUnwrap(c["input"] as? Int)
            let input = inputs[index]
            let flags = try XCTUnwrap(c["flags"] as? String)
            let z = try XCTUnwrap(zips[flags])
            let block = try XCTUnwrap(CardText.blockFromZip(try utf(b64(z["config"]))))
            let tokens = try XCTUnwrap(CardText.tokensFromZip(try utf(b64(z["cmdline"]))))
            let what = "\(c["name"] as? String ?? "?") / \(flags)"

            let cfg = CardText.apply(try b64(input["config"]), exists: true) { CardText.editConfig($0, block: block) }
            XCTAssertEqual(cfg.changed, c["config_written"] as? Bool, "\(what): config written")
            XCTAssertEqual(sha(cfg.bytes), c["config_sha256"] as? String, "\(what): config.txt bytes")
            XCTAssertEqual(cfg.bytes.count, c["config_length"] as? Int, "\(what): config length")
            let cmd = CardText.apply(try b64(input["cmdline"]), exists: true) { CardText.editCmdline($0, wanted: tokens) }
            XCTAssertEqual(cmd.changed, c["cmdline_written"] as? Bool, "\(what): cmdline written")
            XCTAssertEqual(sha(cmd.bytes), c["cmdline_sha256"] as? String, "\(what): cmdline.txt bytes")
            XCTAssertEqual(cmd.bytes.count, c["cmdline_length"] as? Int, "\(what): cmdline length")

            // Again over its own output: nothing changes.
            let cfg2 = CardText.apply(cfg.bytes, exists: true) { CardText.editConfig($0, block: block) }
            XCTAssertTrue(!cfg2.changed && cfg2.bytes == cfg.bytes, "\(what): config twice")
            let cmd2 = CardText.apply(cmd.bytes, exists: true) { CardText.editCmdline($0, wanted: tokens) }
            XCTAssertTrue(!cmd2.changed && cmd2.bytes == cmd.bytes, "\(what): cmdline twice")
            n += 1
        }
        XCTAssertGreaterThanOrEqual(n, 50)
    }

    func testApJsonIsTheServersBytes() throws {
        let ap = try XCTUnwrap(try goldens()["ap_json"] as? [String: Any])
        let got = CardApJson.bytes(ssid: try XCTUnwrap(ap["ssid"] as? String), psk: try XCTUnwrap(ap["psk"] as? String))
        XCTAssertEqual(got, try b64(ap["bytes"]))
        XCTAssertNil(CardApJson.bytes(ssid: "tab\there", psk: "k7tqmzr4wd"))
        XCTAssertEqual(CardApJson.bytes(ssid: "a\"b\\c", psk: "x").map { String(decoding: $0, as: UTF8.self) },
                       "{\n  \"ssid\": \"a\\\"b\\\\c\",\n  \"psk\": \"x\"\n}")
    }

    func testPythonsWhitespaceLinesAndUTF8Replacement() {
        let T = CardText.text, S = CardText.string
        XCTAssertEqual(CardText.pySplit(T("a\u{00a0}b\u{2003}c\u{001f}d ")).map(S), ["a", "b", "c", "d"])
        XCTAssertEqual(S(CardText.pyStrip(T("\u{1c}\t x \u{3000}"))), "x")
        XCTAssertEqual(S(CardText.pyFirstLine(T("one\u{0b}two"))), "one")
        // "\r\n" is one Swift Character; these must still be two scalars.
        XCTAssertEqual(S(CardText.read(Array("a\r\nb\rc".utf8))), "a\nb\nc")
        XCTAssertEqual(CardText.read(Array("a\r\nb".utf8)).count, 3)
        XCTAssertEqual(S(CardText.decodeUTF8Replacing([0x63, 0x61, 0x66, 0xE9, 0x21])), "caf\u{FFFD}!")
        XCTAssertEqual(S(CardText.decodeUTF8Replacing([0xC3, 0x28])), "\u{FFFD}(")
        XCTAssertEqual(S(CardText.decodeUTF8Replacing([0xE2, 0x82])), "\u{FFFD}")
        XCTAssertEqual(S(CardText.decodeUTF8Replacing([0xED, 0xA0])), "\u{FFFD}\u{FFFD}")
        XCTAssertEqual(S(CardText.decodeUTF8Replacing([0xF0, 0x9F, 0x98, 0x80])), "\u{1F600}")
        XCTAssertEqual(CardText.decodeUTF8Replacing([0xEF, 0xBB, 0xBF, 0x78]).map { $0.value }, [0xFEFF, 0x78])
    }

    func testAZipWhoseAdditionsArentDomovoisShapeIsRefused() {
        let m = CardText.marker, e = CardText.endMarker, hook = CardText.firstrunToken
        XCTAssertNil(CardText.blockFromZip("dtparam=x=1\n"))
        XCTAssertNil(CardText.blockFromZip(m + "\n" + e))
        XCTAssertNil(CardText.blockFromZip(m + "\r\nx=1\r\n" + e + "\r\n"))
        XCTAssertNil(CardText.blockFromZip(m + "\n#comment\n" + e + "\n"))
        XCTAssertNil(CardText.tokensFromZip("quiet\n"))
        XCTAssertNil(CardText.tokensFromZip(hook + "\nmore\n"))
        XCTAssertNil(CardText.tokensFromZip(hook + "  x=1\n"))
        XCTAssertNotNil(CardText.tokensFromZip(hook + " x=1\n"))
    }

    func testTheSetupPasswordIsCheckedInHouseholdWords() {
        XCTAssertNil(CardSetupPassword.problem("k7tq mzr4wd"))
        XCTAssertEqual(CardSetupPassword.normalize(" K7TQ mzr4WD "), "k7tqmzr4wd")
        XCTAssertNotNil(CardSetupPassword.problem(""))
        XCTAssertTrue(CardSetupPassword.problem("k7tqmzr4w0")?.contains("never use 0, 1, l or o") ?? false)
        XCTAssertTrue(CardSetupPassword.problem("k7tqmzr4w!")?.contains("isn't in these passwords") ?? false)
        XCTAssertEqual(CardSetupPassword.problem("k7tqmzr4"), "That's 8 characters; the password has 10.")
    }
}
