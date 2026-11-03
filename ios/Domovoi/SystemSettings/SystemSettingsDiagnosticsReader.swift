import DomovoiKit
import Foundation
import MetricKit

/// The **one** file in this app that imports MetricKit.
///
/// It turns whatever iOS has handed over about this app into
/// ``SystemSettingsDiagnosticFacts``, which is plain data, so the log, the
/// report, the de-duplication and the card are all testable without an SDK
/// (`SystemSettingsProblemLog.swift`'s heading says why that line is drawn
/// here and not further in).
///
/// ### Every symbol below was read off the SDK, not remembered
///
/// This item's map was written on Windows, where there is no SDK, and said so:
/// *"verify every MetricKit symbol against the SDK on the Mac before writing
/// against it"*. Done on 2026-11-02, by reading
/// `iPhoneOS.sdk/.../MetricKit.framework/Headers/*.h` and then type-checking a
/// probe with every call in this file against
/// `arm64-apple-ios17.0-simulator`. What that settled:
///
///  - **`MXMetricManager.shared.pastDiagnosticPayloads` exists and reads
///    without a subscriber** (`MXMetricManager.h:45`, `ios(14.0)`). That is
///    what makes this item need no launch-time registration and therefore no
///    second shared change: the card absorbs what is there when it is opened.
///    `addSubscriber:` and `didReceiveDiagnosticPayloads:` are **not** used —
///    a subscriber would have to be installed in `DomovoiApp`, which is not
///    this item's file.
///  - The four arrays on `MXDiagnosticPayload` are each **optional**
///    (`nullable` in `MXDiagnosticPayload.h:36-60`), so each is `?? []` below.
///  - `timeStampEnd` is `nonnull` (`:72`) and is the only time a payload
///    carries that is about when the trouble was; `timeStampBegin` is the
///    window's start.
///  - A hang's duration is an `NSMeasurement<NSUnitDuration *>`
///    (`MXHangDiagnostic.h:35`), hence `.converted(to: .milliseconds).value`
///    rather than a number.
///  - A call-stack tree answers `JSONRepresentation` and nothing else
///    (`MXCallStackTree.h:28`) — there is no pre-rendered stack text on iOS,
///    so ``trace(of:)`` carries that JSON.
///  - `appLaunchDiagnostics` (`:54`) is deliberately **not** read: a slow
///    launch is not a problem report, it has no Android counterpart, and
///    ``SystemSettingsProblemKind`` has no case that could hold one.
///
/// ### What is not, and why there is no control for it
///
///  - **No "refresh" and no live feed.** The OS decides when payloads arrive,
///    after the fact; ``SystemSettingsProblemsModel`` says why a button
///    promising otherwise is not drawn.
///  - **Nothing is uploaded, and nothing registers a handler.** This reads a
///    record iOS already wrote. There is no `NSSetUncaughtExceptionHandler`
///    and no signal handler anywhere in the app — the privacy card's "no
///    crash-reporting service" claim depends on that staying true.
///  - **No payloads in a Simulator.** iOS writes these for a real install, so
///    every stub-house run reads an empty array and the card shows its empty
///    state. The UI case asserts exactly that rather than pretending a
///    Simulator can produce a crash record.
enum SystemSettingsDiagnosticsReader {

    /// How much of one call-stack tree is kept.
    ///
    /// A tree's JSON is unbounded — every frame of every thread — and the log
    /// keeps ten of them in a file this app rewrites. The report's own cut
    /// (``systemSettingsMaxShareChars``, 100 000) is applied to the whole
    /// report, so without a per-trace cut one enormous tree could be the
    /// entire report and push the other nine problems out of it.
    static let maxTraceChars = 8_000

    /// Everything iOS has handed over, flattened, newest-first order left to
    /// the log (``SystemSettingsProblemLog/trimmed(_:)`` sorts).
    @MainActor
    static func facts() -> [SystemSettingsDiagnosticFacts] {
        facts(in: MXMetricManager.shared.pastDiagnosticPayloads)
    }

    /// The flattening, over payloads handed in — so the mapping can be read,
    /// and argued about, without the singleton.
    static func facts(in payloads: [MXDiagnosticPayload]) -> [SystemSettingsDiagnosticFacts] {
        payloads.flatMap { payload in
            let atMs = Int64(payload.timeStampEnd.timeIntervalSince1970 * 1000)
            var out: [SystemSettingsDiagnosticFacts] = []
            for crash in payload.crashDiagnostics ?? [] {
                out.append(
                    SystemSettingsDiagnosticFacts(
                        kind: .crash,
                        atMs: atMs,
                        detail: detail(of: crash),
                        trace: trace(of: crash.callStackTree)
                    )
                )
            }
            for hang in payload.hangDiagnostics ?? [] {
                out.append(
                    SystemSettingsDiagnosticFacts(
                        kind: .hang,
                        atMs: atMs,
                        hangMs: Int64(hang.hangDuration.converted(to: .milliseconds).value),
                        detail: appLine(hang),
                        trace: trace(of: hang.callStackTree)
                    )
                )
            }
            for cpu in payload.cpuExceptionDiagnostics ?? [] {
                out.append(
                    SystemSettingsDiagnosticFacts(
                        kind: .cpu,
                        atMs: atMs,
                        detail: join([
                            appLine(cpu),
                            seconds("processor time", cpu.totalCPUTime),
                            seconds("sampled over", cpu.totalSampledTime),
                        ]),
                        trace: trace(of: cpu.callStackTree)
                    )
                )
            }
            for disk in payload.diskWriteExceptionDiagnostics ?? [] {
                out.append(
                    SystemSettingsDiagnosticFacts(
                        kind: .diskWrite,
                        atMs: atMs,
                        detail: join([
                            appLine(disk),
                            "wrote "
                                + Self.number(disk.totalWritesCaused.converted(to: .megabytes).value)
                                + " MB",
                        ]),
                        trace: trace(of: disk.callStackTree)
                    )
                )
            }
            return out
        }
    }

    // MARK: - what each kind says about itself

    /// A crash's own facts, in the order a person reads them: what iOS called
    /// it, then the numbers whoever reads a stack needs.
    ///
    /// `exceptionType`, `exceptionCode` and `signal` are `NSNumber?`
    /// (`MXCrashDiagnostic.h:51-66`), so each is unwrapped rather than
    /// interpolated — `Optional(6)` in a report somebody is reading is noise.
    private static func detail(of crash: MXCrashDiagnostic) -> String? {
        join([
            appLine(crash),
            crash.terminationReason,
            crash.exceptionType.map { "exception type \($0)" },
            crash.exceptionCode.map { "exception code \($0)" },
            crash.signal.map { "signal \($0)" },
        ])
    }

    /// Which build of this app the trouble happened in.
    ///
    /// **Worth a line of its own**, and it is the one thing the report header
    /// cannot say: the header describes the app that is *reading* the report,
    /// and a payload iOS kept across an update is about the one before it. A
    /// stack read against the wrong build is a stack read against the wrong
    /// line numbers.
    private static func appLine(_ diagnostic: MXDiagnostic) -> String {
        let build = diagnostic.metaData.applicationBuildVersion
        return "in app \(diagnostic.applicationVersion) (\(build))"
    }

    private static func seconds(_ what: String, _ measurement: Measurement<UnitDuration>) -> String {
        "\(what) \(number(measurement.converted(to: .seconds).value)) s"
    }

    /// One decimal, POSIX — the same rule ``systemSettingsFormatTime(_:timeZone:)``
    /// follows, and for the same reason: this text is read beside a stack
    /// trace and must not change shape with the reader's region.
    private static func number(_ value: Double) -> String {
        String(format: "%.1f", locale: Locale(identifier: "en_US_POSIX"), value)
    }

    private static func join(_ lines: [String?]) -> String? {
        let kept = lines.compactMap { line -> String? in
            guard let line, !line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                return nil
            }
            return line
        }
        return kept.isEmpty ? nil : kept.joined(separator: " · ")
    }

    /// The call-stack tree as text, cut to ``maxTraceChars``.
    ///
    /// `JSONRepresentation` is the only thing a tree answers, so this is JSON
    /// and the card draws it monospaced and horizontally scrollable, as
    /// Android draws its own stack text (`ProblemReports.kt:143-151`). Nil for
    /// a tree that answered nothing, so ``SystemSettingsProblemsModel/hasTrace(_:)``
    /// leaves the `show details` toggle off a row with nothing to reveal.
    private static func trace(of tree: MXCallStackTree) -> String? {
        let text = String(decoding: tree.jsonRepresentation(), as: UTF8.self)
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return String(text.prefix(maxTraceChars))
    }
}

// MARK: - this app, as the report header describes it

/// The three header lines, read off the bundle and the device.
///
/// A free function beside the reader rather than inside it: nothing here is
/// MetricKit's, and the card wants it on appear whether or not a payload
/// exists. The *shape* of the lines is
/// ``systemSettingsReportHeader(appVersion:build:debugBuild:systemVersion:deviceModel:nowMs:timeZone:)``,
/// which is in the package with tests; this only reads the four facts.
///
/// **No server address, no token, no account** — Android's `Diagnostics.kt:196-197`,
/// and the reason the package function takes a fixed list of parameters rather
/// than anything that walks a store.
///
/// **Nonisolated, and that is a requirement rather than a preference.**
/// ``SystemSettingsProblemsModel`` takes its header as `@Sendable () -> [String]`
/// so the card can read it off the main actor, so nothing here may touch a
/// main-actor API. That is why the iOS version comes from
/// `ProcessInfo.operatingSystemVersion` and not `UIDevice.current.systemVersion`,
/// which is main-actor isolated in Swift 6 — the same reading, from the API
/// that is free to ask.
func systemSettingsHeaderLines() -> [String] {
    let info = Bundle.main.infoDictionary
    let os = ProcessInfo.processInfo.operatingSystemVersion
    return systemSettingsReportHeader(
        appVersion: info?["CFBundleShortVersionString"] as? String,
        build: info?["CFBundleVersion"] as? String,
        debugBuild: systemSettingsIsDebugBuild,
        systemVersion: "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)",
        deviceModel: systemSettingsDeviceModel(),
        nowMs: Int64(Date().timeIntervalSince1970 * 1000)
    )
}

/// Whether this is a debug build, as the version line and the report header
/// both say (`appVersionText`, `Diagnostics.kt:203`).
///
/// One constant rather than a `#if` at each of the two call sites: two
/// compile-time branches for one fact is two places for them to disagree.
let systemSettingsIsDebugBuild: Bool = {
    #if DEBUG
    return true
    #else
    return false
    #endif
}()

/// The hardware identifier — `iPhone15,2` — not `UIDevice.model`, which
/// answers the word `iPhone` on every iPhone ever made.
///
/// Android's header carries `Build.MANUFACTURER`/`MODEL`
/// (`Diagnostics.kt:197-212`) for the same purpose: whoever reads a stack
/// needs to know which phone produced it. `utsname` is the only API that says,
/// and it is POSIX rather than UIKit, so it needs no entitlement and no
/// permission.
///
/// On a Simulator this answers the *host* architecture (`arm64`), which is
/// honest: a Simulator is not a phone, and a report made on one should not
/// claim to be from hardware.
///
/// Nonisolated, for ``systemSettingsHeaderLines()``'s reason — which is also
/// why a blank answer falls back to a word and not to `UIDevice.current.model`.
func systemSettingsDeviceModel() -> String {
    var system = utsname()
    uname(&system)
    let identifier = withUnsafeBytes(of: &system.machine) { raw in
        String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
    }
    return identifier.isEmpty ? SystemSettingsStrings.versionUnknown : identifier
}

/// What the About card's version line says
/// (``systemSettingsAppVersionText(version:debugBuild:)``, Android's
/// `appVersionText`).
///
/// Read from `CFBundleShortVersionString`, which XcodeGen fills from
/// `MARKETING_VERSION` (`ios/project.yml`). A missing key reads as
/// ``SystemSettingsStrings/versionUnknown``, which is also what a mistyped key
/// would read as — so the UI case asserting the line is **not** "unknown" is
/// asserting this read works.
func systemSettingsVersionLine() -> String {
    systemSettingsAppVersionText(
        version: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String,
        debugBuild: systemSettingsIsDebugBuild
    )
}
