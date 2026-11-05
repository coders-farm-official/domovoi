import DomovoiKit
import SwiftUI
import UIKit

/// Settings → About & updates: **this phone's problem reports**.
///
/// A port of `ProblemReportsCard` (`android/.../ui/screens/settings/ProblemReports.kt:56-161`),
/// read on 2026-11-02. Every rule is ``SystemSettingsProblemsModel``'s and
/// ``SystemSettingsProblemLog``'s — what is kept, in what order, what the
/// report says, which rows have a trace — and this draws them.
///
/// The problems come from MetricKit and from nothing else
/// (``SystemSettingsDiagnosticsReader``, and `SystemSettingsProblemLog.swift`'s
/// heading for what that rules out). Nothing is uploaded, by design: Android's
/// `Diagnostics.kt:31-34` says so about itself and it is as true here.
///
/// ### What is not, and why there is no control for it
///
///  - **`copy all`, `share` and `clear`, with nothing recorded.** Android
///    returns from the card before drawing the row (`ProblemReports.kt:72-78`)
///    and ``SystemSettingsProblemsModel/hasProblems`` is that early return.
///    Three controls over an empty list are three that cannot do what they
///    say. The same rule puts `show details` only on a row that has a trace.
///  - **A refresh control.** The OS hands diagnostics over on its own
///    schedule; a button labelled "refresh" would promise control over
///    Apple's timing. ``SystemSettingsProblemsModel/load(facts:)`` on appear
///    is the only moment there is anything new to absorb.
///  - **"no app found to share with"** (`ProblemReports.kt:89`). `ShareLink`
///    is the system share sheet, which is always there; the branch has no
///    iPhone counterpart, so it is not drawn as one.
///  - **Nothing at all, before the file has been read.**
///    ``SystemSettingsProblemsModel/loaded`` gates the whole body: "nothing
///    recorded" is a claim and it must not be made before the read lands.
struct SystemSettingsProblemsCard: View {

    let model: ShellModel

    @State private var problems: SystemSettingsProblemsModel?

    var body: some View {
        PanelCard(
            title: SystemSettingsStrings.problemsTitle,
            sub: SystemSettingsStrings.problemReportsIntro
        ) {
            if let problems, problems.loaded {
                body(of: problems)
            } else {
                // No empty state and no buttons until the read has landed.
                // A spinner with a label, so VoiceOver has something to say
                // and the card is never a silent gap.
                ProgressView()
                    .accessibilityLabel(SystemSettingsStrings.problemsTitle)
            }
        }
        .task {
            if problems == nil {
                problems = SystemSettingsProblemsModel(
                    // `URL.applicationSupportDirectory` is the same place
                    // ``SystemSettingsProblemStore/applicationSupport(fileManager:)``
                    // finds, by the non-optional API: on iOS that directory
                    // always exists, and an optional store here would mean a
                    // card that can go missing for a reason nobody could see.
                    store: SystemSettingsProblemStore(
                        directory: URL.applicationSupportDirectory
                            .appendingPathComponent("Diagnostics", isDirectory: true)
                    ),
                    header: systemSettingsHeaderLines
                )
            }
            // **The MetricKit read happens here**, at the moment the card
            // appears, which is why the model takes a closure and not a
            // value: `pastDiagnosticPayloads` is what iOS has handed over by
            // now, and re-absorbing the same payloads is harmless (the log
            // de-duplicates and the store writes nothing when nothing
            // changed).
            await problems?.load(facts: { SystemSettingsDiagnosticsReader.facts() })
        }
    }

    @ViewBuilder private func body(of problems: SystemSettingsProblemsModel) -> some View {
        if problems.hasProblems {
            VStack(alignment: .leading, spacing: 10) {
                actions(problems)
                // By index, and not by a key off the problem: two problems
                // of different kinds can share a millisecond, so `atMs`
                // alone is not an identity — the model's own key is
                // ``SystemSettingsProblemsModel/id(of:)``, kind **and**
                // time, and ``SystemSettingsProblem`` is not `Hashable`.
                // Ten rows in a plain stack, so index identity costs
                // nothing: a clear empties the list rather than reordering
                // it.
                ForEach(problems.problems.indices, id: \.self) { at in
                    SystemSettingsProblemRow(
                        model: model,
                        problems: problems,
                        problem: problems.problems[at]
                    )
                }
            }
        } else {
            Text(SystemSettingsStrings.problemsEmpty)
                .font(.callout)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// `copy all`, `share`, `clear` (`ProblemReports.kt:84-93`).
    ///
    /// Android puts them in one horizontally scrolling row. Here they are a
    /// `ViewThatFits` of that row and a column, because an iPhone at an
    /// accessibility size cannot hold three labels on a line and a row that
    /// scrolls sideways is a row a VoiceOver user has to discover.
    @ViewBuilder private func actions(_ problems: SystemSettingsProblemsModel) -> some View {
        // **Rendered once, here.** `ViewThatFits` builds every candidate to
        // measure it, so a `problems.report()` inside `buttons` ran twice per
        // body pass — and the card redraws on every `show details` tap,
        // because `expanded` is tracked on an `@Observable` model. With ten
        // problems each carrying an 8 000-character call-stack tree
        // (`SystemSettingsDiagnosticsReader.maxTraceChars`) that was ~160 KB
        // of string building on the main actor per tap, for a `ShareLink`
        // nobody may ever touch. The pasteboard write and the share item are
        // the same text by definition, so one render serves both.
        let report = problems.report()
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { buttons(problems, report: report) }
            VStack(alignment: .leading, spacing: 8) { buttons(problems, report: report) }
        }
        // The clear is a confirmation, as Android's is
        // (`ProblemReports.kt:106-115`): `destructive` on the yes, and the
        // dialog's own words.
        .confirmationDialog(
            SystemSettingsStrings.clearProblemsTitle,
            isPresented: Binding(
                get: { problems.confirmingClear },
                set: { problems.confirmingClear = $0 }
            ),
            titleVisibility: .visible
        ) {
            Button(SystemSettingsStrings.clear, role: .destructive) {
                Task { await problems.clear() }
            }
        } message: {
            Text(SystemSettingsStrings.clearProblemsBody)
        }
    }

    /// `report` is ``SystemSettingsProblemsModel/report()`` already rendered by
    /// the caller — see ``actions(_:)`` for why it is not rendered here.
    @ViewBuilder private func buttons(
        _ problems: SystemSettingsProblemsModel,
        report: String
    ) -> some View {
        Button(SystemSettingsStrings.copyAll) {
            UIPasteboard.general.string = report
            // A momentary acknowledgement, which is what the shell's toast is
            // for (``ShellModel/showToast(_:)``) — and Android's own surface
            // for this one (`ProblemReports.kt:86`).
            model.showToast(SystemSettingsStrings.problemReportCopied)
        }
        // Android's `OutlinedButton`s (`ProblemReports.kt:84`, `:88`): no
        // fill. The untinted `.bordered` took the app's ``Color/brandInk``
        // tint (`DomovoiApp.swift:232`) and read it on a wash of itself, about
        // 4.06:1 on a card (unit mx-ink, InkButtonStyles.swift).
        .buttonStyle(InkOutlineButtonStyle())
        ShareLink(
            item: report,
            subject: Text(SystemSettingsStrings.problemReportSubject)
        ) {
            Text(SystemSettingsStrings.share)
        }
        .buttonStyle(InkOutlineButtonStyle())
        Button(SystemSettingsStrings.clear) {
            problems.confirmingClear = true
        }
        .foregroundStyle(Color.fgMuted)
        .frame(minHeight: 44)
    }
}

/// One recorded problem: what it was, when, what iOS said, and its stack.
///
/// `ProblemItem` (`ProblemReports.kt:119-161`). The trace toggle is **in the
/// model, not here** — `remember` in a lazy row survives on Android and does
/// not on iOS, so an expanded trace would collapse itself the moment the row
/// scrolled out (``SystemSettingsProblemsModel/isShowingTrace(_:)``).
private struct SystemSettingsProblemRow: View {

    let model: ShellModel
    let problems: SystemSettingsProblemsModel
    let problem: SystemSettingsProblem

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            header
            Text(problem.summary)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
            if let detail = problem.detail,
               !detail.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Text(detail)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if problems.isShowingTrace(problem), let trace = problem.trace {
                // Monospaced, unwrapped and scrolled sideways, as Android
                // draws it (`ProblemReports.kt:143-151`): a stack read with
                // its lines wrapped is a stack nobody can read.
                ScrollView(.horizontal) {
                    Text(trace)
                        .font(.caption2.monospaced())
                        .foregroundStyle(Color.fgMuted)
                        .lineLimit(nil)
                        .textSelection(.enabled)
                }
                .frame(maxHeight: 220)
            }
            rowActions
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        // **A stroke, not a fill.** Android puts each problem on its `sunken`
        // surface; this target has no such token, and the obvious stand-in —
        // `Color(.secondarySystemBackground)` — is the canvas that
        // ``Color/fgMuted`` was *not* measured against (NIGHT-RULES §3), so
        // every muted line in this row would be a contrast finding waiting
        // for the first phone that has a crash to report. An outline groups
        // the row and leaves the card's own canvas underneath it.
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(.quaternary, lineWidth: 1)
        )
    }

    /// Both of this row's rows become columns at an accessibility size, the
    /// way ``SystemSettingsBarRow`` does (`SystemSettingsLayoutPanel.swift:257-271`).
    ///
    /// `header` is the worst of the two: `high processor use` beside
    /// ``systemSettingsFormatTime(_:timeZone:)``'s fixed 26 characters of
    /// `yyyy-MM-dd HH:mm:ss +0000`, where at AX5 neither fits its share of an
    /// iPhone's width and both wrap into tall narrow columns.
    ///
    /// **Unverified by the suite, and unverifiable by it.** iOS writes no
    /// MetricKit diagnostics on a Simulator, so no run on this track can
    /// photograph a row at all — the audit case only ever sees the empty
    /// state, which is the same blindness the row's stroke is argued under
    /// above. It is written the way the rule says and checked by reading.
    @Environment(\.dynamicTypeSize) private var typeSize

    @ViewBuilder private var header: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) { headerParts }
        } else {
            HStack(spacing: 8) { headerParts }
        }
    }

    @ViewBuilder private var headerParts: some View {
        Group {
            // The kind pill. `errInk`/`warnInk` as the text and the outline,
            // never `.red`/`.orange`: Android's `Tone.Err`/`Tone.Warn` with
            // this app's measured ink tokens
            // (`ProblemReports.kt:131`, `DomovoiApp.swift:336-360`).
            Text(problem.kind.label)
                .font(.caption2)
                .padding(.horizontal, 7)
                .padding(.vertical, 3)
                .foregroundStyle(problem.kind.isWarning ? Color.warnInk : Color.errInk)
                .overlay(
                    Capsule().stroke(
                        problem.kind.isWarning ? Color.warnInk : Color.errInk,
                        lineWidth: 1
                    )
                )
                .fixedSize(horizontal: false, vertical: true)
            Text(systemSettingsFormatTime(problem.atMs))
                .font(.caption2.monospaced())
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var rowActions: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) { rowActionParts }
                .font(.footnote)
                .foregroundStyle(Color.brandInk)
        } else {
            HStack(spacing: 4) { rowActionParts }
                .font(.footnote)
                .foregroundStyle(Color.brandInk)
        }
    }

    @ViewBuilder private var rowActionParts: some View {
        Group {
            if problems.hasTrace(problem) {
                Button(
                    problems.isShowingTrace(problem)
                        ? SystemSettingsStrings.hideDetails
                        : SystemSettingsStrings.showDetails
                ) {
                    problems.toggleTrace(problem)
                }
                .frame(minHeight: 44)
            }
            Button(SystemSettingsStrings.copy) {
                UIPasteboard.general.string = problems.report(for: problem)
                model.showToast(SystemSettingsStrings.copied)
            }
            .frame(minHeight: 44)
        }
    }
}
