import XCTest
@testable import Domovoi
import DomovoiKit

/// The app target's own unit tests. Almost everything worth testing lives in
/// `DomovoiKit`, where the tests run on the Mac with no Simulator at all; this
/// target exists for the things that need the app bundle, and for now it
/// checks only that the app links the package and carries the Info.plist keys
/// the pairing flow depends on.
///
/// Not compiled yet: there was no Mac when it was written.
final class DomovoiAppTests: XCTestCase {

    func testTheAppLinksDomovoiKit() {
        XCTAssertTrue(CleartextPolicy.cleartextPermitted("192.168.1.20"))
        XCTAssertFalse(CleartextPolicy.cleartextPermitted("203.0.113.5"))
    }

    /// The Local Network prompt's wording is the I0 gate on Kamron's phone,
    /// and App Transport Security is the platform half of
    /// ``CleartextPolicy``. Both are written in `ios/project.yml`, generated
    /// into the Info.plist, and easy to lose in a regeneration — so the build
    /// says so here.
    /// `Bundle.main` is the HOST APP's bundle in a unit-test target with a
    /// test host, which is how this target is set up in `ios/project.yml`.
    /// TODO(verify-at-mac): if XcodeGen did not wire TEST_HOST, this reads the
    /// test bundle instead and every key below is nil — check that first.
    func testTheInfoPlistCarriesTheKeysPairingNeeds() throws {
        let info = try XCTUnwrap(Bundle.main.infoDictionary)
        let usage = try XCTUnwrap(info["NSLocalNetworkUsageDescription"] as? String)
        XCTAssertTrue(usage.contains("Domovoi"), usage)
        XCTAssertFalse(usage.isEmpty)

        let ats = try XCTUnwrap(info["NSAppTransportSecurity"] as? [String: Any])
        XCTAssertEqual(ats["NSAllowsLocalNetworking"] as? Bool, true)
        let domains = try XCTUnwrap(ats["NSExceptionDomains"] as? [String: Any])
        for suffix in ["home.arpa", "lan", "internal", "home"] {
            let domain = try XCTUnwrap(domains[suffix] as? [String: Any], suffix)
            XCTAssertEqual(domain["NSExceptionAllowsInsecureHTTPLoads"] as? Bool, true, suffix)
            XCTAssertEqual(domain["NSIncludesSubdomains"] as? Bool, true, suffix)
        }
        XCTAssertNil(ats["NSAllowsArbitraryLoads"], "the app never asks for plain http to anywhere")

        // Apple's speech recognition is not used (owner answer Q12: our own
        // Moonshine), so its prompt must never appear.
        XCTAssertNil(info["NSSpeechRecognitionUsageDescription"])

        XCTAssertEqual(info["UIBackgroundModes"] as? [String], ["audio"])
        XCTAssertEqual(info["UIFileSharingEnabled"] as? Bool, true)
        XCTAssertEqual(info["LSSupportsOpeningDocumentsInPlace"] as? Bool, true)
    }

    /// Apple takes three integers and nothing else in the short version
    /// string, and the build number is the shared YYMMDDNN sequence (plan
    /// 4.4). `1.0.0-rc2` would be rejected at upload, which is a slow way to
    /// find out.
    func testTheVersionNumbersAreTheShapeAppleTakes() throws {
        let info = try XCTUnwrap(Bundle.main.infoDictionary)
        let short = try XCTUnwrap(info["CFBundleShortVersionString"] as? String)
        let parts = short.split(separator: ".", omittingEmptySubsequences: false)
        XCTAssertEqual(parts.count, 3, short)
        for part in parts {
            XCTAssertFalse(part.isEmpty, short)
            XCTAssertTrue(part.allSatisfy { $0.isASCII && $0.isNumber }, short)
        }
        let build = try XCTUnwrap(info["CFBundleVersion"] as? String)
        XCTAssertEqual(build.count, 8, "YYMMDDNN: \(build)")
        XCTAssertTrue(build.allSatisfy { $0.isASCII && $0.isNumber }, build)
    }
}
