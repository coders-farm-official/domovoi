import Foundation
import XCTest
@testable import DomovoiKit

/// The iPhone twin of
/// `android/.../ui/screens/settings/OpenSourceLicencesTest.kt`.
///
/// Android reads the list the APK carries, made by Gradle from the variant's
/// dependency graph. Swift packages carry no licence metadata, so there is
/// nothing comparable to read: what the build pins is `Package.resolved`, and
/// the decision is ``Licences/rules``. So this test holds the two against
/// each other —
///
/// - every package the build resolved is under a licence decided by hand
///   (a ``LicenceRule``), and that licence is permissive;
/// - every rule still matches something, or is marked as not yet linked in
///   `ios/licences/README.md`;
/// - no analytics, advertising or crash-reporting package is in the app
///   (the privacy card says so);
/// - `ios/licences/README.md` names every rule, so the card and the list
///   cannot drift apart.
///
/// Nothing is linked in this batch, so the `Package.resolved` part of it is
/// dormant: it starts biting the first time a package is added, which is the
/// moment it is needed. The rest runs today.
///
/// First run on 2026-10-28: the `#filePath` walk lands where it should and the
/// test passes, reporting "no Package.resolved anywhere under ios/ — nothing is
/// linked yet". The `Package.resolved` shape (v2 and v3 are both handled) is
/// still unexercised, and stays so until the first package is added.
final class LicencesTests: XCTestCase {

    // ---- the rules themselves (runs with no dependencies at all) -----------

    func testEveryRuleIsPermissiveAndDistinct() {
        for rule in Licences.rules {
            XCTAssertTrue(
                Licences.permissive.contains(rule.licence),
                "\(rule.title): \(rule.licence) is not a permissive licence"
            )
            XCTAssertFalse(rule.identities.isEmpty, rule.title)
            for identity in rule.identities {
                XCTAssertEqual(identity, identity.lowercased(), "identities are compared lowercased")
            }
        }
        let identities = Licences.rules.flatMap(\.identities)
        XCTAssertEqual(identities.count, Set(identities).count, "a package has one rule")
        let titles = Licences.rules.map(\.title)
        XCTAssertEqual(titles.count, Set(titles).count, "a rule has its own title")
    }

    /// MPL-2.0 is allowed by the policy only for unmodified third-party code
    /// compiled into something else (Eigen inside sherpa-onnx), so it must
    /// never become the licence of a package we link. GPL in any form never.
    func testTheForbiddenLicencesAreNotPermissive() {
        for licence in ["GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1", "LGPL-3.0", "MPL-2.0", "SSPL-1.0"] {
            XCTAssertFalse(Licences.permissive.contains(licence), licence)
        }
    }

    func testTheKindsWeNeverShipAreRecognised() {
        for name in ["firebase-ios-sdk", "FirebaseCrashlytics", "sentry-cocoa",
                     "https://github.com/bugsnag/bugsnag-cocoa", "amplitude-swift"] {
            XCTAssertTrue(Licences.isForbiddenKind(name), name)
        }
        for name in ["onnxruntime-swift-package-manager", "litert-lm", "zipfoundation", "sherpa-onnx"] {
            XCTAssertFalse(Licences.isForbiddenKind(name), name)
        }
    }

    // ---- what the build resolved -------------------------------------------

    func testEveryResolvedPackageHasAPermissiveRuleAndIsNotAnalytics() throws {
        let resolvedFiles = Self.resolvedFiles()
        if resolvedFiles.isEmpty {
            // No package has been added yet. Say so rather than pass quietly.
            print("no Package.resolved anywhere under ios/ — nothing is linked yet")
            return
        }
        var unruled: [String] = []
        var forbidden: [String] = []
        for file in resolvedFiles {
            for pin in try Self.pins(in: file) {
                if Licences.isForbiddenKind(pin.identity) || Licences.isForbiddenKind(pin.location) {
                    forbidden.append("\(pin.identity) (\(pin.location))")
                    continue
                }
                guard let rule = Licences.rule(for: pin.identity) else {
                    unruled.append("\(pin.identity) (\(pin.location))")
                    continue
                }
                XCTAssertTrue(
                    Licences.permissive.contains(rule.licence),
                    "\(pin.identity): the rule says \(rule.licence), which is not permissive"
                )
            }
        }
        XCTAssertEqual(
            unruled, [],
            "packages with no licence rule — read the licence at its source, add a rule in Licences.swift "
                + "and a row in ios/licences/README.md"
        )
        XCTAssertEqual(forbidden, [], "analytics, advertising or crash reporting is never in this app")
    }

    // ---- the card and the list stay together -------------------------------

    func testTheLicencesReadmeNamesEveryRule() throws {
        let readme = Self.repositoryRoot
            .appendingPathComponent("ios/licences/README.md")
        let text = try String(contentsOf: readme, encoding: .utf8)
        for rule in Licences.rules {
            for identity in rule.identities {
                XCTAssertTrue(text.lowercased().contains(identity), "\(identity) is not in ios/licences/README.md")
            }
            XCTAssertTrue(text.contains(rule.licence), "\(rule.title): \(rule.licence) is not in the README")
        }
        XCTAssertTrue(
            text.contains("espeak-ng"),
            "the README must keep saying why sherpa-onnx's prebuilt iOS xcframeworks are never used"
        )
    }

    // ---- reading the repo --------------------------------------------------

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up.
    static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private struct Pin {
        let identity: String
        let location: String
    }

    /// Every `Package.resolved` the iOS build can produce: one per local
    /// package, and the one Xcode keeps inside the generated project.
    private static func resolvedFiles() -> [URL] {
        let candidates = [
            "ios/Packages/DomovoiKit/Package.resolved",
            "ios/Packages/DomovoiPhonemizer/Package.resolved",
            "ios/Domovoi.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved",
        ]
        return candidates
            .map { repositoryRoot.appendingPathComponent($0) }
            .filter { FileManager.default.fileExists(atPath: $0.path) }
    }

    /// `Package.resolved` v2 and v3 keep the pins at the top level; v1 kept
    /// them under `object`. All three are read, because which one SwiftPM
    /// writes depends on the tools version of the package it resolves.
    private static func pins(in file: URL) throws -> [Pin] {
        let data = try Data(contentsOf: file)
        let root = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        let raw = (root["pins"] as? [[String: Any]])
            ?? ((root["object"] as? [String: Any])?["pins"] as? [[String: Any]])
            ?? []
        return raw.map { pin in
            Pin(
                identity: (pin["identity"] as? String) ?? (pin["package"] as? String) ?? "?",
                location: (pin["location"] as? String) ?? (pin["repositoryURL"] as? String) ?? ""
            )
        }
    }
}
