import XCTest
@testable import DomovoiKit

/// The licences card's rows, the privacy points, and the dashboard link.
final class SystemSettingsLicencesTests: XCTestCase {

    // MARK: - the §5 guard

    /// **The one mistake this file exists to prevent.** "Port the licences
    /// card" naturally means porting Android's list — ONNX Runtime 1.28.0,
    /// sherpa-onnx 1.13.8, LiteRT-LM 0.18.0, CMUdict, the Piper voices, the
    /// Liberation fonts, CameraX, ZXing — and **only ONNX Runtime is in this
    /// app**, at its own version. A card is a licence claim; a row for a
    /// package nobody links is a claim about software that is not there.
    func testNoRowNamesSomethingThisAppDoesNotLink() {
        let parts = systemSettingsLicenceParts()
        XCTAssertEqual([SystemSettingsStrings.applesFrameworks, "ONNX Runtime 1.24.2"], parts.map(\.title))
        let text = parts.map { [$0.title, $0.licence, $0.source ?? "", $0.note ?? ""].joined(separator: " ") }
            .joined(separator: "\n")
            .lowercased()
        for absent in [
            "1.28.0", "sherpa", "litert", "cmudict", "piper", "liberation",
            "camerax", "zxing", "androidx", "okhttp", "coil", "kotlin",
            "guava", "gson", "espeak",
        ] {
            XCTAssertFalse(text.contains(absent), "\(absent) is Android's, not this app's")
        }
    }

    /// ONNX Runtime is linked (room mode), so the card draws its row, with
    /// both notices it ships, and not the "links nothing" sentence.
    func testOnnxRuntimeIsLinkedWithItsNotices() {
        XCTAssertEqual(["onnxruntime-swift-package-manager"], systemSettingsLinkedIdentities)
        XCTAssertFalse(systemSettingsLinksNothing())
        let onnx = systemSettingsLicenceParts().first { $0.title == "ONNX Runtime 1.24.2" }
        XCTAssertEqual("MIT", onnx?.licence)
        XCTAssertEqual(["onnxruntime/LICENSE", "onnxruntime/ThirdPartyNotices.txt"], onnx?.notices)
        XCTAssertTrue(onnx?.note?.contains("Microsoft Corporation") == true)
        // The sentence is still right for a build that links nothing.
        XCTAssertTrue(systemSettingsLinksNothing(linked: []))
        XCTAssertTrue(SystemSettingsStrings.licencesNothingLinked.contains("no third-party package"))
    }

    /// `noticeChunks` (`LicencesCard.kt:274-275`): 40 lines a piece, nothing
    /// lost, the empty text one empty piece.
    func testANoticeIsCutIntoFortyLinePieces() {
        let text = (1...85).map(String.init).joined(separator: "\n")
        let chunks = systemSettingsNoticeChunks(text)
        XCTAssertEqual(3, chunks.count)
        XCTAssertEqual(text, chunks.joined(separator: "\n"))
        XCTAssertTrue(chunks.first?.hasPrefix("1\n2\n") == true)
        XCTAssertTrue(chunks.last?.hasPrefix("81\n") == true)
        XCTAssertEqual([""], systemSettingsNoticeChunks(""))
        XCTAssertEqual("onnxruntime/LICENSE", systemSettingsNoticeLabel("notices/onnxruntime/LICENSE"))
        XCTAssertEqual("onnxruntime/LICENSE", systemSettingsNoticeLabel("onnxruntime/LICENSE"))
    }

    /// The guard is worth nothing if it can only be checked against the empty
    /// case: with a package linked, its row comes from its ``LicenceRule``
    /// and from nowhere else — which is the procedure
    /// `Licences.swift:11-13` describes.
    func testALinkedPackageGetsItsRowFromItsRule() {
        let parts = systemSettingsLicenceParts(linked: ["zipfoundation"])
        XCTAssertEqual(2, parts.count)
        XCTAssertFalse(systemSettingsLinksNothing(linked: ["zipfoundation"]))
        let rule = Licences.rule(for: "zipfoundation")
        XCTAssertNotNil(rule)
        XCTAssertEqual(rule?.title, parts[1].title)
        XCTAssertEqual(rule?.licence, parts[1].licence)
        XCTAssertEqual(rule?.source, parts[1].source)
        XCTAssertEqual("MIT", parts[1].licence)
    }

    /// A linked package with no rule is a licence nobody read: the card draws
    /// no row for it rather than inventing one. `LicencesTests` is what fails
    /// for it.
    func testAPackageWithNoRuleGetsNoRow() {
        let parts = systemSettingsLicenceParts(linked: ["some-package-nobody-read"])
        XCTAssertEqual(1, parts.count)
        XCTAssertEqual(SystemSettingsStrings.applesFrameworks, parts[0].title)
    }

    /// Apple's row carries no source: there is nowhere to send anyone, and an
    /// empty source line reads as a mistake.
    func testApplesRowIsPartOfTheOsAndLinksNowhere() {
        XCTAssertNil(systemSettingsApplePart.source)
        XCTAssertEqual("Part of the OS", systemSettingsApplePart.licence)
        XCTAssertTrue(systemSettingsApplePart.note?.contains("MetricKit") == true,
                      "MetricKit is what the problem-report card reads")
    }

    /// The one part of Android's card that ports word for word: it is about
    /// the **server's** downloads, which are the same whatever phone is
    /// looking at them.
    func testTheModelLicencesNoteAndLinkAreAndroidsOwn() {
        XCTAssertTrue(SystemSettingsStrings.modelLicencesNote.contains("hey_jarvis is non-commercial, CC BY-NC-SA 4.0"))
        XCTAssertTrue(SystemSettingsStrings.modelLicencesNote.contains("Llama 3.2 Community License"))
        XCTAssertEqual("licences of what Domovoi downloads", SystemSettingsStrings.modelLicencesLink)
        XCTAssertEqual(
            "https://github.com/coders-farm-official/domovoi/blob/main/docs/LICENSES.md",
            systemSettingsModelLicencesURL
        )
    }

    // MARK: - the privacy policy link (PrivacyCard.kt:30)

    /// https only, and nothing at all when it is not a published page — "no
    /// control that cannot do what it says", in Android's own words.
    func testOnlyAPublishedHttpsPolicyIsLinked() {
        XCTAssertNotNil(systemSettingsPrivacyPolicyLink())
        XCTAssertEqual(systemSettingsPrivacyPolicyURL, systemSettingsPrivacyPolicyLink()?.absoluteString)
        XCTAssertNil(systemSettingsPrivacyPolicyLink(""))
        XCTAssertNil(systemSettingsPrivacyPolicyLink("   "))
        XCTAssertNil(systemSettingsPrivacyPolicyLink("http://example.com/policy"))
        XCTAssertNil(systemSettingsPrivacyPolicyLink("example.com/policy"))
        XCTAssertNil(systemSettingsPrivacyPolicyLink("https://"))
        XCTAssertNotNil(systemSettingsPrivacyPolicyLink("  https://codersfarm.example/privacy  "))
    }

    // MARK: - the privacy points

    /// Android's sentence promises an app-wide backup opt-out that iOS does
    /// not have (`PrivacyCard.kt:45`), so the claim is split. Shipping the
    /// Android wording would be a false privacy claim, and this one is read
    /// on a store listing too.
    func testTheBackupSentenceIsNotAndroidsAndSeparatesTheTwoCases() {
        let storage = SystemSettingsPrivacy.storage
        XCTAssertFalse(
            storage.contains("stays in its private storage on this phone, outside cloud backups"),
            "that is Android's claim, and it is not true of an iPhone build"
        )
        XCTAssertTrue(storage.contains("household token and the problem reports are kept out of backups"))
        XCTAssertTrue(storage.contains("part of this phone's own backup"))
    }

    /// The two sentences that do port verbatim.
    func testTheOtherTwoPointsAreAndroidsWordForWord() {
        XCTAssertEqual(
            "Problem reports stay on this phone until you delete them; the app sends none of them "
                + "anywhere unless you share one yourself.",
            SystemSettingsPrivacy.problemReports
        )
        XCTAssertEqual(
            "There is no analytics, advertising or crash-reporting service in the app.",
            SystemSettingsPrivacy.noAnalytics
        )
    }

    /// Room mode and the microphone are gone, because this app has neither.
    /// A claim about a feature the app does not have is the same fault as a
    /// control that cannot act.
    func testNoPointClaimsAFeatureThisAppDoesNotHave() {
        XCTAssertEqual(3, SystemSettingsPrivacy.points.count)
        let text = SystemSettingsPrivacy.points.joined(separator: "\n").lowercased()
        for absent in ["room mode", "microphone", "wake word", "phone chats", "photo of a letter"] {
            XCTAssertFalse(text.contains(absent), "this app has no \(absent)")
        }
    }

    /// The no-analytics sentence is kept true by ``Licences/never`` — the iOS
    /// counterpart of Android's `OpenSourceLicencesTest` — and by this item
    /// installing no crash handler of its own.
    func testTheNoAnalyticsClaimHasSomethingBehindIt() {
        XCTAssertTrue(Licences.isForbiddenKind("firebase-crashlytics"))
        XCTAssertTrue(Licences.isForbiddenKind("https://github.com/getsentry/sentry-cocoa"))
        XCTAssertFalse(Licences.isForbiddenKind("zipfoundation"))
        for identity in systemSettingsLinkedIdentities {
            XCTAssertFalse(Licences.isForbiddenKind(identity))
        }
    }

    // MARK: - the dashboard link (SettingsModels.kt:49-63)

    func testTheDashboardUrlIsTheSavedAddressAndTheHash() {
        XCTAssertEqual(
            "http://10.0.0.42:6369/#settings",
            systemSettingsDashboardURL(serverURL: "http://10.0.0.42:6369")
        )
        XCTAssertEqual(
            "http://10.0.0.42:6369/#settings/configuration",
            systemSettingsDashboardURL(serverURL: "http://10.0.0.42:6369", section: "configuration")
        )
        XCTAssertEqual("#settings", systemSettingsDashboardHash)
    }

    func testATrailingSlashIsStrippedAndWhitespaceIgnored() {
        XCTAssertEqual(
            "http://a/#settings/configuration",
            systemSettingsDashboardURL(serverURL: "  http://a/  ", section: "configuration")
        )
        XCTAssertEqual(
            "http://a/#settings",
            systemSettingsDashboardURL(serverURL: "http://a///")
        )
    }

    /// An **empty** section is the same as none: `#settings`, with no
    /// trailing slash. The Kotlin's `isNullOrEmpty` covers both and it is
    /// easy to lose one half.
    func testAnEmptySectionIsTheSameAsNone() {
        XCTAssertEqual("http://a/#settings", systemSettingsDashboardURL(serverURL: "http://a", section: ""))
        XCTAssertEqual("http://a/#settings", systemSettingsDashboardURL(serverURL: "http://a", section: nil))
    }

    /// Nil with no server: the caller disables "Open on the dashboard" and
    /// draws ``ShellStrings/noServerForDashboard`` instead.
    func testWithNoServerThereIsNoUrlAtAll() {
        XCTAssertNil(systemSettingsDashboardURL(serverURL: nil))
        XCTAssertNil(systemSettingsDashboardURL(serverURL: ""))
        XCTAssertNil(systemSettingsDashboardURL(serverURL: "   "))
        XCTAssertNil(systemSettingsDashboardURL(serverURL: "/"))
        XCTAssertFalse(ShellStrings.noServerForDashboard.isEmpty)
    }

    /// Advanced's dashboard key is `configuration`, not `advanced`: an older
    /// server has no `advanced` section (`SettingsScreen.kt:152`). Building
    /// the hash from the route's own key is the natural mistake, and this is
    /// the pin for it.
    func testAdvancedPointsAtTheConfigurationSection() {
        XCTAssertEqual("configuration", systemSettingsAdvancedDashboardSection)
        let url = systemSettingsDashboardURL(
            serverURL: "http://10.0.0.42:6369",
            section: systemSettingsAdvancedDashboardSection
        )
        XCTAssertEqual("http://10.0.0.42:6369/#settings/configuration", url)
        XCTAssertFalse(url?.hasSuffix("advanced") == true)
    }
}
