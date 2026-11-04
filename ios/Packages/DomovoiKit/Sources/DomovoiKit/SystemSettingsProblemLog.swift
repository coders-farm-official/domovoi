import Foundation

/// This phone's own record of what went wrong in **this app**: the log, its
/// rules, and the report it renders.
///
/// A port of `android/.../diagnostics/ProblemLog.kt`, read on 2026-11-02.
/// Everything here is plain Swift with no MetricKit and no file system, for
/// the reason the Kotlin gives about itself: it is the part the tests can
/// drive. The one file that imports MetricKit is
/// `ios/Domovoi/SystemSettings/SystemSettingsDiagnosticsReader.swift`, and all
/// it does is flatten a payload into ``SystemSettingsDiagnosticFacts``.
///
/// ### Where the problems come from, and what that rules out
///
/// **MetricKit, and nothing else.** iOS writes crash and hang diagnostics for
/// this app, on device, and hands them to the app itself: no network, no
/// permission prompt, the same shape as Android's exit history — the system's
/// record, read by the app, kept by the app, sent nowhere.
///
/// ### What is not, and why there is no control for it
///
///  - **An uncaught-exception handler and a main-thread watchdog**
///    (`Diagnostics.kt:50-64`, `MainThreadWatchdog.kt`). Doing that here means
///    `NSSetUncaughtExceptionHandler` plus signal handlers in a shipping app —
///    crash-reporting machinery this app has a rule against carrying
///    (`Licences.swift:55-65`), and the privacy card's claim that there is
///    none depends on it staying true.
///  - **Android's exit history** (`Diagnostics.kt:107-142`) and the five kinds
///    that only it can report: `not responding`, `killed for memory`,
///    `failed to start`, `killed for resource use`, `killed while frozen`.
///    iOS has no counterpart of `getHistoricalProcessExitReasons`, so those
///    kinds would be categories nothing could ever file under.
///  - **`pid` and `source`** (`ProblemLog.kt:35-41`). Both exist in the Kotlin
///    to reconcile the app's own crash record with Android's record of the
///    same death (`importingExits`). With one source there is nothing to
///    reconcile, and the report does not claim a process id it never had.
///    De-duplication is by kind, time and
///    ``SystemSettingsProblem/ordinal`` — **not** by kind and time alone:
///    that argument covers reconciling two sources and says nothing about
///    two diagnostics inside one payload, which share a timestamp.
///  - **No live feed.** The OS decides when payloads arrive — after the fact,
///    not at the moment of the crash — which is why the card's wording
///    promises a record and never "now".
public enum SystemSettingsProblemKind: String, Codable, Sendable, CaseIterable {
    /// `MXCrashDiagnostic`. Android's `ProblemKind.CRASH`, same word.
    case crash
    /// `MXHangDiagnostic`: the main thread stopped responding. Android's
    /// `ProblemKind.FREEZE`, and ``label`` keeps Android's word for it,
    /// because it is the same event told to the same person.
    case hang
    /// `MXCPUExceptionDiagnostic`.
    case cpu
    /// `MXDiskWriteExceptionDiagnostic`.
    case diskWrite

    /// What the kind pill says. `crash` and `freeze` are Android's words
    /// (`ProblemLog.kt:46`, `:49`); the other two are iOS-only and are
    /// recorded in `IOS-ONLY-STRINGS.md` — Android has no kind for either
    /// because nothing on Android reports them.
    public var label: String {
        switch self {
        case .crash: return "crash"
        case .hang: return "freeze"
        case .cpu: return "high processor use"
        case .diskWrite: return "high disk writes"
        }
    }

    /// Whether the pill is drawn in the warning tone rather than the error
    /// one. Android: only `FREEZE` is a warning (`ProblemReports.kt:131`);
    /// the two iOS-only kinds are resource notices, not failures, so they
    /// read the same way.
    public var isWarning: Bool { self != .crash }
}

/// One thing that went wrong (`ProblemLog.kt:23-42`, minus `pid` and
/// `source`).
public struct SystemSettingsProblem: Equatable, Codable, Sendable {
    public let kind: SystemSettingsProblemKind
    /// When it happened, epoch milliseconds.
    public let atMs: Int64
    /// Which one of its kind it was **inside the payload it arrived in**.
    ///
    /// Not Android's `pid` by another name, and not an ordering: it exists
    /// because `atMs` is the payload's `timeStampEnd` and every diagnostic in
    /// one payload therefore shares it (`SystemSettingsDiagnosticsReader`).
    /// `crashDiagnostics` is an array precisely because a day's payload can
    /// hold several crashes, so without this a build that crashed three times
    /// de-duplicated down to one problem and the card under-reported in
    /// silence. 0 for anything with one of its kind, and 0 for a log written
    /// before this field existed.
    public let ordinal: Int
    /// One line saying what happened.
    public let summary: String
    /// iOS's own details, when there are any.
    public let detail: String?
    /// A trimmed call-stack text: the crashing thread's, or the main
    /// thread's for a hang.
    public let trace: String?

    public init(
        kind: SystemSettingsProblemKind,
        atMs: Int64,
        ordinal: Int = 0,
        summary: String,
        detail: String? = nil,
        trace: String? = nil
    ) {
        self.kind = kind
        self.atMs = atMs
        self.ordinal = ordinal
        self.summary = summary
        self.detail = detail
        self.trace = trace
    }

    /// Hand-written for one line of it: `ordinal` is **absent** from every log
    /// this app has already written to disk, and a synthesised
    /// `init(from:)` would throw on that key — which
    /// ``systemSettingsDecodeLog(_:)`` turns into an empty log, silently
    /// discarding a person's whole record on the update that added the field.
    /// Everything else is what the compiler would have written.
    public init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        kind = try box.decode(SystemSettingsProblemKind.self, forKey: .kind)
        atMs = try box.decode(Int64.self, forKey: .atMs)
        ordinal = try box.decodeIfPresent(Int.self, forKey: .ordinal) ?? 0
        summary = try box.decode(String.self, forKey: .summary)
        detail = try box.decodeIfPresent(String.self, forKey: .detail)
        trace = try box.decodeIfPresent(String.self, forKey: .trace)
    }
}

/// The stored log: newest first (`ProblemLog.kt:60-96`).
public struct SystemSettingsProblemLog: Equatable, Codable, Sendable {

    public var problems: [SystemSettingsProblem]

    public init(problems: [SystemSettingsProblem] = []) {
        self.problems = problems
    }

    /// How many problems are kept (`ProblemLog.MAX_KEPT`).
    public static let maxKept = 10

    /// Freezes are kept to at most this many, so a run of them cannot push a
    /// crash out of the log (`ProblemLog.MAX_FREEZES`).
    public static let maxFreezes = 4

    /// Add `problem`, newest first, trimmed. One already in the log — same
    /// kind, same time, same place in its payload — is not added twice
    /// (`ProblemLog.with`).
    ///
    /// The Kotlin's third de-duplication key is the process id; see this
    /// file's heading for why there is none here. ``SystemSettingsProblem/ordinal``
    /// is the third key instead, and it is not optional: every diagnostic in
    /// one MetricKit payload shares that payload's `timeStampEnd`, so on
    /// `(kind, atMs)` alone three crashes in a day collapsed into one.
    /// Absorbing the same payload twice still changes nothing, which is the
    /// property this method exists for.
    public func with(_ problem: SystemSettingsProblem) -> SystemSettingsProblemLog {
        let isKnown = problems.contains {
            $0.kind == problem.kind && $0.atMs == problem.atMs && $0.ordinal == problem.ordinal
        }
        if isKnown { return self }
        return SystemSettingsProblemLog(problems: Self.trimmed(problems + [problem]))
    }

    /// Several at once, in one pass — what the card does when it absorbs a
    /// batch of payloads.
    public func with(contentsOf incoming: [SystemSettingsProblem]) -> SystemSettingsProblemLog {
        incoming.reduce(self) { $0.with($1) }
    }

    /// `ProblemLog.kt:88-96`: newest first, the freeze cap, then the total
    /// cap. **The order matters** — capping freezes before taking the ten
    /// keeps a run of them from pushing a crash out, which is the whole
    /// reason the Kotlin has two numbers.
    static func trimmed(_ all: [SystemSettingsProblem]) -> [SystemSettingsProblem] {
        // `sorted(by:)` is not stable in the standard library, and two
        // problems can share a millisecond; the index tiebreak keeps the
        // order deterministic, which a test depends on and a reader does too.
        let newestFirst = all.enumerated()
            .sorted { a, b in
                a.element.atMs == b.element.atMs
                    ? a.offset > b.offset
                    : a.element.atMs > b.element.atMs
            }
            .map(\.element)
        var freezes = 0
        return newestFirst
            .filter { problem in
                guard problem.kind == .hang else { return true }
                freezes += 1
                return freezes <= maxFreezes
            }
            .prefix(maxKept)
            .map { $0 }
    }
}

// MARK: - storage format (ProblemLog.kt:338-344)

/// A damaged or foreign file reads as an **empty log** rather than failing
/// (`decodeLog`). The card is drawn by a store that cannot refuse to load.
public func systemSettingsDecodeLog(_ data: Data?) -> SystemSettingsProblemLog {
    guard let data, !data.isEmpty,
          let log = try? JSONDecoder().decode(SystemSettingsProblemLog.self, from: data)
    else { return SystemSettingsProblemLog() }
    return log
}

public func systemSettingsEncodeLog(_ log: SystemSettingsProblemLog) -> Data {
    (try? JSONEncoder().encode(log)) ?? Data("{\"problems\":[]}".utf8)
}

// MARK: - the shareable report (ProblemLog.kt:346-372)

/// How much of a report is handed to a share sheet
/// (`ProblemReports.kt:181`). Android keeps it under the Binder limit an
/// Intent extra travels through; iOS has no such limit, and the number is
/// kept because the cut is also what stops a pasteboard write of unbounded
/// size — and because a report of a different length on each platform would
/// be a difference with nothing behind it.
public let systemSettingsMaxShareChars = 100_000

/// `yyyy-MM-dd HH:mm:ss Z` in the POSIX locale (`ProblemLog.kt:346`).
///
/// `en_US_POSIX` and a fixed pattern, not a `Date.FormatStyle`: this string
/// is read by whoever gets the report, alongside a stack trace, and must not
/// change shape with the reader's region.
public func systemSettingsFormatTime(
    _ ms: Int64,
    timeZone: TimeZone = .current
) -> String {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = timeZone
    formatter.dateFormat = "yyyy-MM-dd HH:mm:ss Z"
    return formatter.string(from: Date(timeIntervalSince1970: Double(ms) / 1000))
}

/// One problem as text, for copying (`renderProblem`, `ProblemLog.kt:351-358`).
public func systemSettingsRenderProblem(
    _ problem: SystemSettingsProblem,
    timeZone: TimeZone = .current
) -> String {
    var out = problem.kind.label
    out += " at " + systemSettingsFormatTime(problem.atMs, timeZone: timeZone)
    out += SystemSettingsStrings.recordedByIOS
    out += "\n" + problem.summary
    if let detail = problem.detail, !detail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        out += "\n" + detail
    }
    if let trace = problem.trace, !trace.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        out += "\n\n" + trace
    }
    return out
}

/// The whole log as one report: the heading, `header`'s lines, then each
/// problem newest first (`renderReport`, `ProblemLog.kt:360-372`).
///
/// **Not cut here.** Android cuts at the call site (`ProblemReports.kt:69`)
/// and so does the card, through ``systemSettingsCut(_:)``, so that a test
/// can read a whole report and the cut is one rule in one place.
public func systemSettingsRenderReport(
    _ problems: [SystemSettingsProblem],
    header: [String],
    timeZone: TimeZone = .current
) -> String {
    var out = SystemSettingsStrings.problemReportHeading + "\n"
    for line in header { out += line + "\n" }
    if problems.isEmpty {
        out += "\n" + SystemSettingsStrings.noProblemsRecorded + "\n"
        return out
    }
    for (i, problem) in problems.enumerated() {
        out += "\n--- \(i + 1) of \(problems.count) ---\n"
        out += systemSettingsRenderProblem(problem, timeZone: timeZone) + "\n"
    }
    return out
}

/// `take(MAX_SHARE_CHARS)` (`ProblemReports.kt:69`). Characters, as there —
/// not bytes, not UTF-16 units.
public func systemSettingsCut(_ report: String) -> String {
    String(report.prefix(systemSettingsMaxShareChars))
}

/// The app and phone lines at the top of a shared report
/// (`Diagnostics.reportHeader`, `Diagnostics.kt:197-212`).
///
/// **No server address, no token, no account** — Android's comment at
/// `:196-197`, and the reason this is a fixed list of three rather than
/// anything that walks a settings store: only what helps read a stack.
public func systemSettingsReportHeader(
    appVersion: String?,
    build: String?,
    debugBuild: Bool,
    systemVersion: String,
    deviceModel: String,
    nowMs: Int64,
    timeZone: TimeZone = .current
) -> [String] {
    let version = appVersion.flatMap { $0.isEmpty ? nil : $0 } ?? SystemSettingsStrings.versionUnknown
    var first = "app \(version)"
    if let build, !build.isEmpty {
        first += " (\(build)\(debugBuild ? ", debug build" : ""))"
    } else if debugBuild {
        first += " (debug build)"
    }
    return [
        first,
        "iOS \(systemVersion), \(deviceModel)",
        "report made " + systemSettingsFormatTime(nowMs, timeZone: timeZone),
    ]
}

// MARK: - what a MetricKit payload is flattened into

/// One diagnostic, as plain data, with every MetricKit type already gone.
///
/// The reader turns `MXCrashDiagnostic`, `MXHangDiagnostic`,
/// `MXCPUExceptionDiagnostic` and `MXDiskWriteExceptionDiagnostic` into
/// these, at the one place that imports MetricKit, and everything from here
/// on is testable without an SDK — which also means a field MetricKit renames
/// costs one file, not the log and the card with it.
public struct SystemSettingsDiagnosticFacts: Equatable, Sendable {
    public let kind: SystemSettingsProblemKind
    /// When it happened, epoch milliseconds — `timeStampEnd` of the payload
    /// the diagnostic came in.
    public let atMs: Int64
    /// Which one of its kind it was inside that payload — the reader's loop
    /// index. See ``SystemSettingsProblem/ordinal``: without it, two crashes
    /// in one payload are one problem, because `atMs` is the payload's.
    public let ordinal: Int
    /// How long the main thread was stuck, for a hang. Nil for the rest, and
    /// nil for a hang whose payload did not say.
    public let hangMs: Int64?
    /// Anything iOS said about it: the exception type, the signal, the
    /// termination reason — whatever the reader found, already joined.
    public let detail: String?
    /// The call-stack text, already trimmed by the reader.
    public let trace: String?

    public init(
        kind: SystemSettingsProblemKind,
        atMs: Int64,
        ordinal: Int = 0,
        hangMs: Int64? = nil,
        detail: String? = nil,
        trace: String? = nil
    ) {
        self.kind = kind
        self.atMs = atMs
        self.ordinal = ordinal
        self.hangMs = hangMs
        self.detail = detail
        self.trace = trace
    }
}

/// Android's `seconds` (`ProblemLog.kt:330`): `2.3 s`, POSIX-formatted.
func systemSettingsSeconds(_ ms: Int64) -> String {
    String(format: "%.1f s", locale: Locale(identifier: "en_US_POSIX"), Double(ms) / 1000)
}

/// The summary line for each kind.
///
/// `crash` is Android's own sentence (`ProblemLog.kt:176`) and the hang's is
/// Android's freeze sentence with its "(still frozen when saved)" clause
/// dropped — MetricKit only ever reports a hang that is over. The two
/// resource kinds have no Android sentence at all, because nothing on
/// Android reports them; both are in `IOS-ONLY-STRINGS.md`.
func systemSettingsSummary(for facts: SystemSettingsDiagnosticFacts) -> String {
    switch facts.kind {
    case .crash:
        return "The app crashed"
    case .hang:
        guard let hangMs = facts.hangMs, hangMs > 0 else {
            return "The screen stopped responding"
        }
        return "The screen stopped responding for \(systemSettingsSeconds(hangMs))"
    case .cpu:
        return "iOS recorded this app using a lot of processor time"
    case .diskWrite:
        return "iOS recorded this app writing to storage a lot"
    }
}

/// Every diagnostic iOS handed over, as problems the log takes.
public func systemSettingsProblems(
    from facts: [SystemSettingsDiagnosticFacts]
) -> [SystemSettingsProblem] {
    facts.map { fact in
        SystemSettingsProblem(
            kind: fact.kind,
            atMs: fact.atMs,
            ordinal: fact.ordinal,
            summary: systemSettingsSummary(for: fact),
            detail: fact.detail,
            trace: fact.trace
        )
    }
}
