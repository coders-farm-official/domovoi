import XCTest
@testable import DomovoiKit

/// The problem-report file and the card's model
/// (``SystemSettingsProblemStore``, ``SystemSettingsProblemsModel``).
final class SystemSettingsProblemStoreTests: XCTestCase {

    private var directory: URL!

    override func setUp() {
        super.setUp()
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("systemsettings-\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDown() {
        if let directory { try? FileManager.default.removeItem(at: directory) }
        super.tearDown()
    }

    private func store() -> SystemSettingsProblemStore {
        SystemSettingsProblemStore(directory: directory)
    }

    private func facts(
        _ kind: SystemSettingsProblemKind,
        _ atMs: Int64,
        hangMs: Int64? = nil
    ) -> SystemSettingsDiagnosticFacts {
        SystemSettingsDiagnosticFacts(kind: kind, atMs: atMs, hangMs: hangMs)
    }

    // MARK: - the file

    /// A directory that does not exist yet, which is every first launch.
    func testAMissingFileReadsAsAnEmptyLogAndDoesNotThrow() async {
        let log = await store().read()
        XCTAssertEqual(SystemSettingsProblemLog(), log)
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: directory.path),
            "a read creates nothing: the card is drawn before anything is recorded"
        )
    }

    func testWhatIsAbsorbedComesBackFromANewStore() async {
        let problems = systemSettingsProblems(from: [facts(.crash, 2_000), facts(.hang, 1_000, hangMs: 2_460)])
        let written = await store().absorb(problems)
        XCTAssertEqual(2, written.problems.count)

        // A second store over the same directory: the app quit and started.
        let reread = await store().read()
        XCTAssertEqual(written, reread)
        XCTAssertEqual([2_000, 1_000], reread.problems.map(\.atMs))
        XCTAssertEqual("The screen stopped responding for 2.5 s", reread.problems[1].summary)
    }

    /// The card absorbs the same payloads every time it is opened — iOS keeps
    /// handing over what it already handed over — so the log's own
    /// de-duplication is what keeps the list from growing by itself.
    func testAbsorbingTheSamePayloadsTwiceChangesNothing() async {
        let problems = systemSettingsProblems(from: [facts(.crash, 2_000), facts(.hang, 1_000)])
        let one = await store().absorb(problems)
        let two = await store().absorb(problems)
        XCTAssertEqual(one, two)
        XCTAssertEqual(2, two.problems.count)
    }

    func testClearEmptiesTheFileAndTheFileStaysReadable() async {
        let store = store()
        await store.absorb(systemSettingsProblems(from: [facts(.crash, 1)]))
        await store.clear()
        XCTAssertEqual(SystemSettingsProblemLog(), await store.read())
        XCTAssertEqual(SystemSettingsProblemLog(), await self.store().read())
    }

    /// A file some other build, or some other thing, wrote: an empty log and
    /// a card that still draws (``systemSettingsDecodeLog(_:)``'s rule). A
    /// card that cannot draw because of its own file is the worse answer.
    func testADamagedFileIsOverwrittenRatherThanTakingTheCardDown() async {
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? Data("}} not json {{".utf8)
            .write(to: directory.appendingPathComponent("problems.json"))

        let store = store()
        XCTAssertEqual(SystemSettingsProblemLog(), await store.read())
        let after = await store.absorb(systemSettingsProblems(from: [facts(.crash, 5)]))
        XCTAssertEqual(1, after.problems.count)
        XCTAssertEqual(after, await self.store().read())
    }

    /// The claim the privacy card makes: the reports are kept out of this
    /// phone's backup. iOS has no app-wide opt-out, so it is this flag or
    /// nothing (see ``SystemSettingsPrivacy/storage``).
    func testTheDirectoryIsKeptOutOfBackups() async throws {
        await store().absorb(systemSettingsProblems(from: [facts(.crash, 1)]))
        let values = try directory.resourceValues(forKeys: [.isExcludedFromBackupKey])
        XCTAssertEqual(true, values.isExcludedFromBackup)
    }

    /// The whole log is kept to ten with at most four freezes, through the
    /// file as well as in memory.
    func testTheCapsHoldThroughTheFile() async {
        let store = store()
        var ms: Int64 = 0
        for _ in 1...9 {
            ms += 1_000
            await store.absorb(systemSettingsProblems(from: [facts(.hang, ms)]))
        }
        for _ in 1...9 {
            ms += 1_000
            await store.absorb(systemSettingsProblems(from: [facts(.crash, ms)]))
        }
        let log = await self.store().read()
        XCTAssertEqual(SystemSettingsProblemLog.maxKept, log.problems.count)
        XCTAssertLessThanOrEqual(
            log.problems.filter { $0.kind == .hang }.count,
            SystemSettingsProblemLog.maxFreezes
        )
    }

    // MARK: - the card's model

    @MainActor
    private func model(_ store: SystemSettingsProblemStore) -> SystemSettingsProblemsModel {
        SystemSettingsProblemsModel(store: store, header: { ["app 1.0.0 (3)"] })
    }

    @MainActor
    func testWithNothingRecordedThereAreNoButtonsAndTheEmptyStateIsTheCard() async {
        let model = model(store())
        XCTAssertFalse(model.loaded, "'nothing recorded' is a claim, and not made before the read")
        await model.load(facts: { [] })
        XCTAssertTrue(model.loaded)
        XCTAssertFalse(model.hasProblems, "no copy all, no share, no clear")
        XCTAssertEqual([], model.problems)
        XCTAssertFalse(SystemSettingsStrings.problemsEmpty.isEmpty)
    }

    @MainActor
    func testTheCardAbsorbsWhatIOsHandedOverAndRendersTheReport() async {
        let model = model(store())
        await model.load(facts: { [self.facts(.crash, 1_000)] })
        XCTAssertTrue(model.hasProblems)
        XCTAssertEqual(1, model.problems.count)
        let report = model.report()
        XCTAssertTrue(report.hasPrefix(SystemSettingsStrings.problemReportHeading))
        XCTAssertTrue(report.contains("app 1.0.0 (3)"))
        XCTAssertTrue(report.contains("The app crashed"))
        XCTAssertEqual(report, model.report(for: model.problems[0]))
    }

    @MainActor
    func testClearEmptiesTheCardAndTheDialogCloses() async {
        let store = store()
        let model = model(store)
        await model.load(facts: { [self.facts(.crash, 1_000)] })
        model.confirmingClear = true
        await model.clear()
        XCTAssertFalse(model.confirmingClear)
        XCTAssertFalse(model.hasProblems)
        XCTAssertEqual(SystemSettingsProblemLog(), await store.read())
    }

    /// **The read-after-await case for this model.** A clear that runs inside
    /// a load's round trip is the newer truth: the list the load started with
    /// must not be written back over an emptied log.
    @MainActor
    func testAClearInsideALoadsRoundTripWins() async {
        let store = store()
        await store.absorb(systemSettingsProblems(from: [facts(.crash, 1_000)]))
        let model = model(store)
        await model.load(facts: { [] })
        XCTAssertEqual(1, model.problems.count)

        // The facts closure runs on the main actor BEFORE the load's own
        // await, so the task it starts gets its turn at exactly the
        // suspension this case is about.
        var clearing: Task<Void, Never>?
        await model.load(facts: {
            clearing = Task { @MainActor in await model.clear() }
            return []
        })
        await clearing?.value
        XCTAssertFalse(model.hasProblems, "the clear is newer than the load that was in flight")
        XCTAssertEqual(SystemSettingsProblemLog(), await store.read())
    }

    /// A trace toggle lives in the model, not in the row: a lazy row is
    /// rebuilt as it scrolls and would collapse itself.
    @MainActor
    func testTheTraceToggleIsPerProblemAndOnlyWhereThereIsATrace() async {
        let store = store()
        await store.absorb([
            SystemSettingsProblem(kind: .crash, atMs: 2_000, summary: "The app crashed", trace: "frame"),
            SystemSettingsProblem(kind: .hang, atMs: 1_000, summary: "The screen stopped responding", trace: "  "),
        ])
        let model = model(store)
        await model.load(facts: { [] })
        let crash = model.problems[0]
        let hang = model.problems[1]
        XCTAssertTrue(model.hasTrace(crash))
        XCTAssertFalse(model.hasTrace(hang), "a blank trace gets no toggle: it would reveal nothing")
        XCTAssertNotEqual(model.id(of: crash), model.id(of: hang))

        XCTAssertFalse(model.isShowingTrace(crash))
        model.toggleTrace(crash)
        XCTAssertTrue(model.isShowingTrace(crash))
        XCTAssertFalse(model.isShowingTrace(hang), "one row open does not open the other")
        model.toggleTrace(crash)
        XCTAssertFalse(model.isShowingTrace(crash))
    }

    @MainActor
    func testAClearForgetsWhichRowsWereOpen() async {
        let store = store()
        await store.absorb([
            SystemSettingsProblem(kind: .crash, atMs: 1, summary: "The app crashed", trace: "frame"),
        ])
        let model = model(store)
        await model.load(facts: { [] })
        model.toggleTrace(model.problems[0])
        let was = model.problems[0]
        await model.clear()
        XCTAssertFalse(model.isShowingTrace(was))
    }
}
