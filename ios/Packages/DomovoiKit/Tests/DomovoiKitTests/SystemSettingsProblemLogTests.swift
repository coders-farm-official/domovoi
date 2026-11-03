import XCTest
@testable import DomovoiKit

/// The problem log, its report, and what a MetricKit payload becomes
/// (``SystemSettingsProblemLog``, ``SystemSettingsDiagnosticFacts``).
///
/// The Android counterpart is `ProblemLogTest.kt`, whose cases about exit
/// history, process ids and the ANR trace parser have no iOS source and are
/// not ported (see ``SystemSettingsProblemKind``'s heading). What is ported
/// is every rule that decides what the card shows.
final class SystemSettingsProblemLogTests: XCTestCase {

    private let utc = TimeZone(identifier: "UTC")!

    private func problem(
        _ kind: SystemSettingsProblemKind,
        _ atMs: Int64,
        summary: String = "s",
        detail: String? = nil,
        trace: String? = nil
    ) -> SystemSettingsProblem {
        SystemSettingsProblem(kind: kind, atMs: atMs, summary: summary, detail: detail, trace: trace)
    }

    // MARK: - the log's rules (ProblemLog.kt:68-96)

    func testTheLogIsNewestFirst() {
        let log = SystemSettingsProblemLog()
            .with(problem(.crash, 1_000))
            .with(problem(.crash, 3_000))
            .with(problem(.crash, 2_000))
        XCTAssertEqual([3_000, 2_000, 1_000], log.problems.map(\.atMs))
    }

    /// Same kind, same time: not added twice. Opening the card re-absorbs
    /// the same payloads every time, so this is what keeps the list from
    /// growing by itself.
    func testTheSameProblemIsNotAddedTwice() {
        let one = problem(.crash, 1_000)
        let log = SystemSettingsProblemLog().with(one).with(one)
        XCTAssertEqual(1, log.problems.count)
    }

    func testTwoKindsAtTheSameMillisecondAreTwoProblems() {
        let log = SystemSettingsProblemLog()
            .with(problem(.crash, 1_000))
            .with(problem(.hang, 1_000))
        XCTAssertEqual(2, log.problems.count)
    }

    func testTenAreKeptAndTheEleventhPushesTheOldestOut() {
        var log = SystemSettingsProblemLog()
        for i in 1...12 {
            log = log.with(problem(.crash, Int64(i) * 1_000))
        }
        XCTAssertEqual(SystemSettingsProblemLog.maxKept, log.problems.count)
        XCTAssertEqual(12_000, log.problems.first?.atMs)
        XCTAssertEqual(3_000, log.problems.last?.atMs, "1 and 2 fell off")
    }

    /// `MAX_FREEZES` exists so that a run of freezes cannot push a crash out
    /// of the log (`ProblemLog.kt:70-72`) — which is the whole reason the
    /// Kotlin has two numbers instead of one, and the order they are applied
    /// in is what makes it work.
    func testARunOfFreezesCannotPushACrashOut() {
        var log = SystemSettingsProblemLog().with(problem(.crash, 1))
        for i in 1...9 {
            log = log.with(problem(.hang, Int64(i) * 1_000))
        }
        let kinds = log.problems.map(\.kind)
        XCTAssertEqual(
            SystemSettingsProblemLog.maxFreezes,
            kinds.filter { $0 == .hang }.count
        )
        XCTAssertTrue(kinds.contains(.crash), "the oldest entry is a crash and it stays")
        XCTAssertEqual(5, log.problems.count)
    }

    /// The four freezes kept are the **newest** four.
    func testTheFreezesKeptAreTheNewest() {
        var log = SystemSettingsProblemLog()
        for i in 1...6 {
            log = log.with(problem(.hang, Int64(i) * 1_000))
        }
        XCTAssertEqual([6_000, 5_000, 4_000, 3_000], log.problems.map(\.atMs))
    }

    func testABatchGoesInThroughTheSameRules() {
        let log = SystemSettingsProblemLog().with(contentsOf: [
            problem(.hang, 1_000), problem(.hang, 2_000), problem(.hang, 3_000),
            problem(.hang, 4_000), problem(.hang, 5_000), problem(.crash, 500),
        ])
        XCTAssertEqual(5, log.problems.count)
        XCTAssertEqual(4, log.problems.filter { $0.kind == .hang }.count)
        XCTAssertEqual(.crash, log.problems.last?.kind)
    }

    // MARK: - the stored file (ProblemLog.kt:338-344)

    func testALogSurvivesARoundTrip() {
        let log = SystemSettingsProblemLog(problems: [
            problem(.crash, 9_000, summary: "The app crashed", detail: "d", trace: "t"),
            problem(.hang, 8_000, summary: "The screen stopped responding for 2.5 s"),
        ])
        XCTAssertEqual(log, systemSettingsDecodeLog(systemSettingsEncodeLog(log)))
    }

    /// `decodeLog`'s rule: a damaged or foreign file is an empty log, not a
    /// failure. The card must draw even when its own file is rubbish.
    func testADamagedFileReadsAsAnEmptyLog() {
        XCTAssertEqual(SystemSettingsProblemLog(), systemSettingsDecodeLog(nil))
        XCTAssertEqual(SystemSettingsProblemLog(), systemSettingsDecodeLog(Data()))
        XCTAssertEqual(SystemSettingsProblemLog(), systemSettingsDecodeLog(Data("not json".utf8)))
        XCTAssertEqual(
            SystemSettingsProblemLog(),
            systemSettingsDecodeLog(Data(#"{"problems":[{"kind":"not-a-kind","atMs":1,"summary":"s"}]}"#.utf8)),
            "a kind this build does not know takes the file with it, rather than crashing the card"
        )
    }

    // MARK: - the report (ProblemLog.kt:346-372)

    func testTheTimeIsFixedShapeWhateverTheRegion() {
        XCTAssertEqual("1970-01-01 00:00:00 +0000", systemSettingsFormatTime(0, timeZone: utc))
        // 2026-11-02T03:04:05Z = 1 793 588 645 s since the epoch.
        XCTAssertEqual("2026-11-02 03:04:05 +0000", systemSettingsFormatTime(1_793_588_645_000, timeZone: utc))
    }

    func testAnEmptyReportSaysSoAndStillCarriesTheHeader() {
        let text = systemSettingsRenderReport([], header: ["app 1.0.0 (3)"], timeZone: utc)
        XCTAssertTrue(text.hasPrefix(SystemSettingsStrings.problemReportHeading))
        XCTAssertTrue(text.contains("app 1.0.0 (3)"))
        XCTAssertTrue(text.contains(SystemSettingsStrings.noProblemsRecorded))
        XCTAssertFalse(text.contains("Android"), "the report must not send its reader to the wrong code")
    }

    func testAReportNumbersEveryProblemAndKeepsItsParts() {
        let text = systemSettingsRenderReport(
            [
                problem(.crash, 0, summary: "The app crashed", detail: "exception", trace: "frame one"),
                problem(.hang, 0, summary: "The screen stopped responding for 2.5 s"),
            ],
            header: ["app 1.0.0 (3)"],
            timeZone: utc
        )
        XCTAssertTrue(text.contains("--- 1 of 2 ---"))
        XCTAssertTrue(text.contains("--- 2 of 2 ---"))
        XCTAssertTrue(text.contains("crash at 1970-01-01 00:00:00 +0000"))
        XCTAssertTrue(text.contains("freeze at "), "the hang is told as Android's freeze")
        XCTAssertTrue(text.contains("exception"))
        XCTAssertTrue(text.contains("frame one"))
        XCTAssertTrue(text.contains(SystemSettingsStrings.recordedByIOS))
    }

    /// A blank detail or trace is left out, not drawn as an empty line
    /// (`renderProblem`'s `takeIf { it.isNotBlank() }`).
    func testABlankDetailOrTraceIsLeftOut() {
        let text = systemSettingsRenderProblem(
            problem(.crash, 0, summary: "The app crashed", detail: "   ", trace: "\n"),
            timeZone: utc
        )
        XCTAssertEqual(
            "crash at 1970-01-01 00:00:00 +0000\(SystemSettingsStrings.recordedByIOS)\nThe app crashed",
            text
        )
    }

    func testTheReportIsCutAtAHundredThousandCharacters() {
        let long = String(repeating: "x", count: 200_000)
        let text = systemSettingsRenderReport(
            [problem(.crash, 0, summary: "The app crashed", trace: long)],
            header: [],
            timeZone: utc
        )
        XCTAssertGreaterThan(text.count, systemSettingsMaxShareChars)
        XCTAssertEqual(systemSettingsMaxShareChars, systemSettingsCut(text).count)
        XCTAssertEqual(100_000, systemSettingsMaxShareChars)
    }

    func testAShortReportIsNotCut() {
        let text = systemSettingsRenderReport([], header: [], timeZone: utc)
        XCTAssertEqual(text, systemSettingsCut(text))
    }

    // MARK: - the report header (Diagnostics.kt:197-212)

    /// Android's own comment: no server address, token or account — only what
    /// helps read a stack. The header is a fixed list of three for that
    /// reason, and this is the claim.
    func testTheHeaderCarriesNoAddressNoTokenAndNoAccount() {
        let lines = systemSettingsReportHeader(
            appVersion: "1.0.0",
            build: "42",
            debugBuild: false,
            systemVersion: "26.1",
            deviceModel: "iPhone15,2",
            nowMs: 0,
            timeZone: utc
        )
        XCTAssertEqual(
            ["app 1.0.0 (42)", "iOS 26.1, iPhone15,2", "report made 1970-01-01 00:00:00 +0000"],
            lines
        )
        let text = lines.joined(separator: "\n").lowercased()
        for banned in ["http", "token", "6369", "@", "household"] {
            XCTAssertFalse(text.contains(banned), "the header must not carry \(banned)")
        }
    }

    func testADebugBuildSaysSoAndAMissingVersionReadsAsUnknown() {
        XCTAssertEqual(
            "app 1.0.0 (42, debug build)",
            systemSettingsReportHeader(
                appVersion: "1.0.0", build: "42", debugBuild: true,
                systemVersion: "26.1", deviceModel: "x", nowMs: 0, timeZone: utc
            ).first
        )
        XCTAssertEqual(
            "app unknown (debug build)",
            systemSettingsReportHeader(
                appVersion: nil, build: nil, debugBuild: true,
                systemVersion: "26.1", deviceModel: "x", nowMs: 0, timeZone: utc
            ).first
        )
        XCTAssertEqual(
            "app unknown",
            systemSettingsReportHeader(
                appVersion: "", build: "", debugBuild: false,
                systemVersion: "26.1", deviceModel: "x", nowMs: 0, timeZone: utc
            ).first
        )
    }

    // MARK: - what a payload becomes

    func testACrashBecomesAProblemWithAndroidsOwnSentence() {
        let problems = systemSettingsProblems(from: [
            SystemSettingsDiagnosticFacts(
                kind: .crash, atMs: 7_000, detail: "EXC_BAD_ACCESS", trace: "frame"
            ),
        ])
        XCTAssertEqual(1, problems.count)
        XCTAssertEqual("The app crashed", problems[0].summary)
        XCTAssertEqual(.crash, problems[0].kind)
        XCTAssertEqual(7_000, problems[0].atMs)
        XCTAssertEqual("EXC_BAD_ACCESS", problems[0].detail)
        XCTAssertEqual("frame", problems[0].trace)
    }

    /// The hang's sentence is Android's freeze sentence with the duration in
    /// Android's own `%.1f s` shape, and **without** its "(still frozen when
    /// saved)" clause: iOS only ever reports a hang that is over.
    func testAHangCarriesItsDurationInAndroidsShape() {
        let withTime = systemSettingsProblems(from: [
            SystemSettingsDiagnosticFacts(kind: .hang, atMs: 1, hangMs: 2_460),
        ])
        XCTAssertEqual("The screen stopped responding for 2.5 s", withTime[0].summary)
        XCTAssertFalse(withTime[0].summary.contains("still frozen"))

        let withoutTime = systemSettingsProblems(from: [
            SystemSettingsDiagnosticFacts(kind: .hang, atMs: 1),
            SystemSettingsDiagnosticFacts(kind: .hang, atMs: 2, hangMs: 0),
        ])
        XCTAssertEqual("The screen stopped responding", withoutTime[0].summary)
        XCTAssertEqual("The screen stopped responding", withoutTime[1].summary)
    }

    func testTheTwoResourceKindsHaveTheirOwnSentences() {
        let problems = systemSettingsProblems(from: [
            SystemSettingsDiagnosticFacts(kind: .cpu, atMs: 1),
            SystemSettingsDiagnosticFacts(kind: .diskWrite, atMs: 2),
        ])
        XCTAssertTrue(problems[0].summary.contains("processor"))
        XCTAssertTrue(problems[1].summary.contains("storage"))
    }

    /// `crash` and `freeze` are Android's words for the same two events, and
    /// only a crash is drawn in the error tone (`ProblemReports.kt:131`).
    func testTheKindLabelsAndTonesMatchAndroid() {
        XCTAssertEqual("crash", SystemSettingsProblemKind.crash.label)
        XCTAssertEqual("freeze", SystemSettingsProblemKind.hang.label)
        XCTAssertFalse(SystemSettingsProblemKind.crash.isWarning)
        XCTAssertTrue(SystemSettingsProblemKind.hang.isWarning)
        XCTAssertEqual(4, SystemSettingsProblemKind.allCases.count)
        for kind in SystemSettingsProblemKind.allCases {
            XCTAssertFalse(kind.label.isEmpty)
        }
    }

    /// The five kinds only Android's exit history can report are **not**
    /// here: a category nothing can ever file under is a promise the card
    /// cannot keep.
    func testTheExitHistoryKindsAreNotClaimed() {
        let labels = SystemSettingsProblemKind.allCases.map(\.label)
        for absent in [
            "not responding", "killed for memory", "failed to start",
            "killed for resource use", "killed while frozen", "ended abruptly",
            "native crash",
        ] {
            XCTAssertFalse(labels.contains(absent), "\(absent) has no iOS source")
        }
    }

    func testTheIntroKeepsAndroidsGuaranteeWordForWord() {
        XCTAssertTrue(
            SystemSettingsStrings.problemReportsIntro.hasSuffix(
                "They stay on the phone: nothing is sent anywhere unless you copy or share it."
            )
        )
        XCTAssertFalse(
            SystemSettingsStrings.problemReportsIntro.contains("Android"),
            "the rewritten half must not keep Android's version talk"
        )
    }
}
