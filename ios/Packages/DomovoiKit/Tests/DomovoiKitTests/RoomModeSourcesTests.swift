import Foundation
import XCTest

/// Room mode's logic stays iOS-15-clean (ROOMMODE-MAP §6.0): the owner may
/// later move `Sources/DomovoiKit/RoomMode/` into a package with an iOS 15
/// floor, so iPhone 6s-X phones can be rooms. The app ships at iOS 18 and the
/// package's platforms say so, so the compiler will NOT catch an iOS 16-18
/// API in this folder; this test does, by name.
///
/// It scans code, not comments: a line whose first non-blank characters are
/// `//` is skipped, and so is everything after `//` on a line. A forbidden
/// name inside a string literal would be caught too, which is the safe side.
final class RoomModeSourcesTests: XCTestCase {

    /// Each: what is forbidden, and why (the iOS it needs, or the rule).
    static let forbidden: [(pattern: String, why: String)] = [
        (#"\bimport\s+Synchronization\b"#, "Synchronization is iOS 18: use RoomLock"),
        (#"\bMutex\b"#, "Mutex is iOS 18: use RoomLock"),
        (#"\bAtomic\s*<"#, "Synchronization's Atomic is iOS 18"),
        (#"\bimport\s+Observation\b"#, "Observation is iOS 17, and room-mode logic stays plain"),
        (#"@Observable\b"#, "Observation is iOS 17, and room-mode logic stays plain"),
        (#"\bDuration\b"#, "Duration is iOS 16: use nanoseconds or TimeInterval"),
        (#"Task\.sleep\s*\(\s*for:"#, "Task.sleep(for:) is iOS 16: use nanoseconds:"),
        (#"Task\.sleep\s*\(\s*until:"#, "Task.sleep(until:) is iOS 16"),
        (#"\b(ContinuousClock|SuspendingClock)\b"#, "Clock is iOS 16"),
        (#"\bclock\s*:\s*\.(continuous|suspending)\b"#, "Clock is iOS 16"),
        (#"\.appending\s*\(\s*(path|component|components)\s*:"#, "URL.appending(path:) is iOS 16"),
        (#"\bRegex\s*[<(]"#, "Regex is iOS 16"),
        (#"(^|[\s(=])#/"#, "regex literals are iOS 16"),
        (#"\bthrows\s*\("#, "typed throws need the Swift 6 runtime on iOS 18"),
        (#"\bNSLock\(\)\.withLock\b"#, "NSLock.withLock is iOS 16: use RoomLock"),
        (#"\bimport\s+(SwiftUI|UIKit)\b"#, "room-mode logic imports no UI framework"),
        (#"\bShellModel\b|\bAppContainer\b|\bPlayerModel\b"#, "room-mode logic never reaches the household app's models"),
    ]

    private static let folder: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<3 { url = url.deletingLastPathComponent() }
        return url.appendingPathComponent("Sources/DomovoiKit/RoomMode")
    }()

    func testRoomModeSourcesUseNothingNewerThanIOS15() throws {
        let files = try FileManager.default.contentsOfDirectory(at: Self.folder, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "swift" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        XCTAssertFalse(files.isEmpty, "no sources in \(Self.folder.path)")
        XCTAssertTrue(files.contains { $0.lastPathComponent == "RoomModeContracts.swift" },
                      "the scan is not looking at the room-mode folder: \(files.map(\.lastPathComponent))")
        let rules = try Self.forbidden.map { (try NSRegularExpression(pattern: $0.pattern), $0.why) }
        var found: [String] = []
        for file in files {
            let text = try String(contentsOf: file, encoding: .utf8)
            for (number, raw) in text.components(separatedBy: "\n").enumerated() {
                let line = Self.code(raw)
                if line.isEmpty { continue }
                let range = NSRange(line.startIndex..., in: line)
                for (rule, why) in rules where rule.firstMatch(in: line, range: range) != nil {
                    found.append("\(file.lastPathComponent):\(number + 1): \(why): \(raw.trimmingCharacters(in: .whitespaces))")
                }
            }
        }
        XCTAssertEqual([], found, "room-mode code must stay iOS-15-clean (ROOMMODE-MAP §6.0)")
    }

    /// The guard is worth nothing if it cannot fire: each rule catches its
    /// own example, and a comment is never caught.
    func testTheGuardCatchesWhatItNames() throws {
        let examples = [
            "import Synchronization", "let m = Mutex(0)", "import Observation", "@Observable final class A {}",
            "let d: Duration = .seconds(1)", "try await Task.sleep(for: .seconds(1))",
            "let c = ContinuousClock()", "url.appending(path: \"x\")", "let r = try Regex(\"a\")",
            "let r = #/a+/#", "func f() throws(MyError) {}", "NSLock().withLock { }", "import SwiftUI",
            "let s: ShellModel",
        ]
        let rules = try Self.forbidden.map { try NSRegularExpression(pattern: $0.pattern) }
        for example in examples {
            let range = NSRange(example.startIndex..., in: example)
            XCTAssertTrue(rules.contains { $0.firstMatch(in: example, range: range) != nil }, "not caught: \(example)")
        }
        XCTAssertEqual("", Self.code("    /// Mutex is iOS 18"))
        XCTAssertEqual("let a = 1 ", Self.code("let a = 1 // Mutex"))
        let fine = ["func f() throws -> Int", "try await Task.sleep(nanoseconds: 1)", "lock.withLock { $0 }"]
        for line in fine {
            let range = NSRange(line.startIndex..., in: line)
            XCTAssertFalse(rules.contains { $0.firstMatch(in: line, range: range) != nil }, "wrongly caught: \(line)")
        }
    }

    /// A line without its comment: nothing for a comment line, the code
    /// before `//` otherwise (a `//` inside a string is rare here, and
    /// cutting at it only scans less).
    static func code(_ line: String) -> String {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("//") { return "" }
        if let cut = line.range(of: "//") { return String(line[..<cut.lowerBound]) }
        return line
    }
}
