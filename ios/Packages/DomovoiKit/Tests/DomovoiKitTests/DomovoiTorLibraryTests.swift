import CryptoKit
import Foundation
import XCTest

/// Away chat's Tor library, ios/Vendor/domovoi-tor/DomovoiTor.xcframework
/// (built from the repository's native/domovoi-tor crate by its
/// build-ios.sh): the committed files are the ones SHA256SUMS records, there
/// is a slice for the iPhone and one for the Simulator, each library has the
/// three C calls `DomovoiTorEngine` makes, the header is the crate's, and no
/// build machine's path is in them. The iPhone twin of Android's
/// `NativeTorLibraryTest`; it reads files, so it runs on the Mac too.
final class DomovoiTorLibraryTests: XCTestCase {

    private static let vendor: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url.appendingPathComponent("ios/Vendor/domovoi-tor")
    }()

    private func sums() throws -> [String: String] {
        let text = try String(contentsOf: Self.vendor.appendingPathComponent("SHA256SUMS"), encoding: .utf8)
        var out: [String: String] = [:]
        for line in text.split(separator: "\n") where !line.isEmpty {
            let parts = line.components(separatedBy: "  ")
            XCTAssertEqual(parts.count, 2, String(line))
            out[parts[1]] = parts[0]
        }
        return out
    }

    func testEveryFileIsCommittedWithItsRecordedHash() throws {
        let sums = try sums()
        XCTAssertEqual(Set(sums.keys), [
            "DomovoiTor.xcframework/Info.plist",
            "DomovoiTor.xcframework/ios-arm64/Headers/domovoi_tor.h",
            "DomovoiTor.xcframework/ios-arm64/Headers/module.modulemap",
            "DomovoiTor.xcframework/ios-arm64/libdomovoi_tor.a",
            "DomovoiTor.xcframework/ios-arm64_x86_64-simulator/Headers/domovoi_tor.h",
            "DomovoiTor.xcframework/ios-arm64_x86_64-simulator/Headers/module.modulemap",
            "DomovoiTor.xcframework/ios-arm64_x86_64-simulator/libdomovoi_tor.a",
        ])
        for (path, want) in sums {
            let data = try Data(contentsOf: Self.vendor.appendingPathComponent(path))
            let got = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            XCTAssertEqual(got, want, path)
        }
    }

    func testTheHeaderIsTheCratesAndTheModuleIsDomovoiTorFFI() throws {
        var repo = Self.vendor
        for _ in 0..<3 { repo = repo.deletingLastPathComponent() }
        let header = try Data(contentsOf: repo.appendingPathComponent("native/domovoi-tor/include/domovoi_tor.h"))
        for slice in ["ios-arm64", "ios-arm64_x86_64-simulator"] {
            let headers = Self.vendor.appendingPathComponent("DomovoiTor.xcframework/\(slice)/Headers")
            XCTAssertEqual(try Data(contentsOf: headers.appendingPathComponent("domovoi_tor.h")), header, slice)
            let map = try String(contentsOf: headers.appendingPathComponent("module.modulemap"), encoding: .utf8)
            XCTAssertTrue(map.contains("module DomovoiTorFFI"), slice)
        }
        let text = String(decoding: header, as: UTF8.self)
        for call in ["int32_t dtor_start(", "int32_t dtor_open(", "void dtor_stop("] {
            XCTAssertTrue(text.contains(call), call)
        }
    }

    /// Mach-O static libraries for iOS: the iPhone's one arm64 slice, the
    /// Simulator's arm64 and x86_64 in one fat file; each has the three C
    /// calls, and no path from the machine that built it.
    func testEachLibraryHasTheCCallsAndNoBuildPaths() throws {
        let plist = try String(contentsOf: Self.vendor.appendingPathComponent("DomovoiTor.xcframework/Info.plist"),
                               encoding: .utf8)
        XCTAssertTrue(plist.contains("<string>ios-arm64</string>"))
        XCTAssertTrue(plist.contains("<string>ios-arm64_x86_64-simulator</string>"))
        for (slice, fat) in [("ios-arm64", false), ("ios-arm64_x86_64-simulator", true)] {
            let data = try Data(contentsOf: Self.vendor.appendingPathComponent("DomovoiTor.xcframework/\(slice)/libdomovoi_tor.a"))
            if fat {
                XCTAssertEqual(Array(data.prefix(4)), [0xCA, 0xFE, 0xBA, 0xBE], "\(slice): a fat file")
            } else {
                XCTAssertEqual(String(decoding: data.prefix(8), as: UTF8.self), "!<arch>\n", "\(slice): an ar archive")
            }
            for symbol in ["_dtor_start", "_dtor_open", "_dtor_stop"] {
                XCTAssertNotNil(data.range(of: Data(symbol.utf8)), "\(slice): \(symbol)")
            }
            for path in ["/Users/", "/home/runner", ".cargo/registry", "/private/var/folders"] {
                XCTAssertNil(data.range(of: Data(path.utf8)), "\(slice): \(path)")
            }
        }
    }
}
