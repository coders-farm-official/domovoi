import Foundation
import Observation

/// The problem-reports card: what is recorded, the text it copies and
/// shares, and the clear.
///
/// A port of `ui/screens/settings/ProblemReports.kt:56-115`, read on
/// 2026-11-02. Android's card is a composable holding four `remember`s and a
/// `LaunchedEffect`; the state is here instead, because the card lives in a
/// lazy list and **a lazy row never holds a `Task`** (NIGHT-RULES §3) — a row
/// scrolled off mid-clear would cancel it and leave the file written but the
/// list not.
///
/// ### What is not, and why there is no control for it
///
///  - **No `copy all`, `share` or `clear` with nothing recorded.** Android
///    returns early from the card before drawing the row of buttons
///    (`ProblemReports.kt:72-78`) and ``hasProblems`` is that early return.
///    Three buttons over an empty list are three controls that cannot do
///    what they say.
///  - **No refresh control.** Android's `Diagnostics.refresh()` re-reads its
///    own store because its own crash handler may have written since
///    (`:65`); here the OS hands diagnostics over on its own schedule, so
///    ``load(facts:)`` on appear is the only moment there is anything new to
///    absorb, and a button labelled "refresh" would promise the person
///    control over Apple's timing.
@MainActor
@Observable
public final class SystemSettingsProblemsModel {

    public private(set) var problems: [SystemSettingsProblem] = []

    /// The app and phone lines at the top of the report
    /// (``systemSettingsReportHeader(appVersion:build:debugBuild:systemVersion:deviceModel:nowMs:timeZone:)``).
    public private(set) var header: [String] = []

    /// False until the first ``load(facts:)`` has finished. The card draws
    /// nothing rather than the empty state while it is false: "nothing
    /// recorded" is a claim, and it must not be made before the file has been
    /// read.
    public private(set) var loaded = false

    /// The clear dialog's presentation, bound by the view
    /// (`ProblemReports.kt:60`, `confirmClear`).
    public var confirmingClear = false

    private let store: SystemSettingsProblemStore
    private let readHeader: @Sendable () -> [String]
    private var expanded: Set<String> = []

    /// Not observed: bumping it must not invalidate a view.
    @ObservationIgnored private var generation = 0

    public init(
        store: SystemSettingsProblemStore,
        header: @escaping @Sendable () -> [String]
    ) {
        self.store = store
        self.readHeader = header
    }

    /// `ProblemReports.kt:72`: with nothing recorded the card is the empty
    /// state and nothing else.
    public var hasProblems: Bool { !problems.isEmpty }

    /// Read the file, fold in whatever iOS has handed over, and build the
    /// report header.
    ///
    /// `facts` is a closure and not a value so that the MetricKit read — the
    /// one thing in this item that needs an SDK — happens at the moment the
    /// card appears rather than when the model is built. It is called **on
    /// this actor and before the await**, which is why it is neither
    /// `@Sendable` nor escaping.
    public func load(facts: () -> [SystemSettingsDiagnosticFacts]) async {
        generation += 1
        let mine = generation
        let lines = readHeader()
        let log = await store.absorb(systemSettingsProblems(from: facts()))
        // The generation was taken BEFORE the await: a clear that ran inside
        // this round trip is the newer truth, and its empty list must not be
        // overwritten by the list this read started with.
        guard mine == generation else { return }
        header = lines
        problems = log.problems
        loaded = true
    }

    /// The dialog's yes (`ProblemReports.kt:112`).
    public func clear() async {
        generation += 1
        confirmingClear = false
        await store.clear()
        problems = []
        expanded = []
    }

    /// The whole log as one report, cut (`ProblemReports.kt:69`).
    public func report() -> String {
        systemSettingsCut(systemSettingsRenderReport(problems, header: header))
    }

    /// One problem as a report of its own, for the row's `copy`
    /// (`ProblemReports.kt:100`).
    public func report(for problem: SystemSettingsProblem) -> String {
        systemSettingsCut(systemSettingsRenderReport([problem], header: header))
    }

    // MARK: - the trace toggle

    /// A stable key for a row: a problem is identified by its kind and its
    /// time, which is also what the log de-duplicates on, so two rows can
    /// never share one.
    public func id(of problem: SystemSettingsProblem) -> String {
        "\(problem.kind.rawValue)@\(problem.atMs)"
    }

    /// Whether this row's call stack is showing. **In the model, not the
    /// row**: `remember` in a lazy row survives on Android and does not here,
    /// so an expanded trace would collapse itself the moment it scrolled out.
    public func isShowingTrace(_ problem: SystemSettingsProblem) -> Bool {
        expanded.contains(id(of: problem))
    }

    /// `show details` / `hide details` (`ProblemReports.kt:154-156`). Only a
    /// problem that has a trace gets the control, so a toggle that reveals
    /// nothing is never drawn.
    public func toggleTrace(_ problem: SystemSettingsProblem) {
        let key = id(of: problem)
        if expanded.contains(key) {
            expanded.remove(key)
        } else {
            expanded.insert(key)
        }
    }

    public func hasTrace(_ problem: SystemSettingsProblem) -> Bool {
        guard let trace = problem.trace else { return false }
        return !trace.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}
