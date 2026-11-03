import DomovoiKit
import SwiftUI

/// Scanning into Documents: glass or feeder, colour, Scan — then a page at a
/// time until Done, and the file the server saved.
///
/// `ScanSheet()` (`ScanSheet.kt:46-201`). The decisions are
/// ``PrintingScanModel``'s.
///
/// ### Leaving mid-scan cancels it
///
/// A scan that has started holds the household's scanner and has pages on the
/// platen that nothing has saved. Dismissing the sheet cancels it
/// (``PrintingScanModel/dismiss()``, `ScanSheet.kt:101-105`) so nothing is
/// half-saved behind the person's back and the scanner is free for whoever is
/// standing at it. That is behaviour, not tidiness — and it is the one place
/// this screen differs from ``PrintSheetView``, which does **not** cancel a
/// print the server has already accepted.
///
/// ### What is not here, and why there is no control for it
///
/// **"What does it say?"** Android's `ScanSheet` takes an `onWhatDoesItSay`
/// parameter and no caller passes one (`ScanSheet.kt:51,178-180`); the button
/// is unreachable in the shipping app. Porting it would ship a control on the
/// strength of a seam nobody wired. Left out, and written up in
/// `design-notes/ios/overnight/printing/DECISIONS.md` as an Android finding.
///
/// **`Open` on a finished scan.** The folder behind this sheet reloads when
/// the scan lands, and tapping the new `.pdf` row in Documents is already the
/// share sheet (``FileKinds/FileAction/save``). A second button doing the same
/// act by another path would be two controls for one thing. The sheet says
/// where the scan went, in the server's own words
/// (``printingScanEndMessage(_:)``).
///
/// **A resolution picker.** 300 dpi, or the nearest the scanner offers
/// (``printingScanResolution(_:)``). Neither platform asks, and the server's
/// `ScanBody` defaults to 300 anyway.
struct ScanSheetView: View {

    let printers: PrintersResponse
    let model: ShellModel
    /// Reload the folder behind the sheet, so the saved scan's row is there.
    ///
    /// `@MainActor` is part of the type, not decoration: the model stores this
    /// as `@MainActor () -> Void` (``PrintingScanModel/init(in:printers:onSaved:)``),
    /// and under Swift 6 a global-actor function type is implicitly
    /// `@Sendable`, so handing it a plain `() -> Void` from inside `.task`
    /// is a non-Sendable conversion the compiler rejects. Declaring it here
    /// makes the caller's closure literal MainActor-isolated at the call
    /// site, which it already was — `FileSheets` builds it in `body`.
    let onSaved: @MainActor () -> Void
    let onClose: () -> Void

    @State private var scan: PrintingScanModel?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let scan {
                        // **The pickers go away once a scan has started.**
                        // Android returns out of the column after drawing
                        // them only while `s == null` (`ScanSheet.kt:115-156`),
                        // so a running or finished scan shows the state line,
                        // the bar, the thumbnails and the buttons and nothing
                        // else. Three greyed menus above "Saved to Documents
                        // as …" are controls that cannot act, which is the
                        // one rule this screen is built on.
                        if scan.scan == nil {
                            pickers(scan)
                        }
                        progress(scan)
                        buttons(scan)
                    } else {
                        ProgressView()
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(PrintingStrings.scanTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(PrintingStrings.close) { onClose() }
                }
            }
        }
        .task {
            if scan == nil {
                scan = PrintingScanModel(
                    in: model.container,
                    printers: printers,
                    onSaved: onSaved
                )
            }
        }
        // **The cancel lives here**, on the view going away, rather than in
        // the model's `deinit`: the sheet and the model go away together, and
        // the request is sent from a detached task for exactly that reason.
        .onDisappear { scan?.dismiss() }
        .onChange(of: scan?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            scan?.clearToast()
        }
    }

    // MARK: - the pickers

    @ViewBuilder
    private func pickers(_ scan: PrintingScanModel) -> some View {
        let controls = scan.controls
        // The scanner's name as a line when there is no picker
        // (`ScanSheet.kt:126`): one scanner is not a choice.
        if let line = scan.scannerLine {
            Text(line)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
        }
        if controls.scanner {
            PrintingMenuRow(
                title: PrintingStrings.scannerLabel,
                chosen: scan.scanner?.displayTitle ?? "",
                options: scan.scanners.map { scanner in
                    PrintingMenuRow.Option(label: scanner.displayTitle) {
                        scan.pick(scanner: scanner)
                    }
                }
            )
        }
        // `glass or feeder` only when the scanner really offers more than one
        // source (``printingScanControls(caps:scannerCount:)``): a picker
        // over one source cannot change anything.
        if !controls.sources.isEmpty {
            PrintingMenuRow(
                title: PrintingStrings.glassOrFeeder,
                chosen: printingSourceLabel(scan.source),
                options: controls.sources.map { value in
                    PrintingMenuRow.Option(label: printingSourceLabel(value)) {
                        scan.pick(source: value)
                    }
                }
            )
        }
        if !controls.colors.isEmpty {
            PrintingMenuRow(
                title: PrintingStrings.colourLabel,
                chosen: printingColorLabel(scan.color ?? ""),
                options: controls.colors.map { value in
                    PrintingMenuRow.Option(label: printingColorLabel(value)) {
                        scan.pick(color: value)
                    }
                }
            )
        }
    }

    // MARK: - how it is going

    @ViewBuilder
    private func progress(_ scan: PrintingScanModel) -> some View {
        if let line = scan.stateLine {
            Text(line)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
        }
        if scan.working {
            ProgressView()
                .progressViewStyle(.linear)
                .accessibilityLabel(PrintingStrings.scanning)
        }
        if let pages = scan.scan?.thumbs, !pages.isEmpty {
            PrintingThumbStrip(thumbs: pages)
        }
        if let ended = scan.endMessage {
            Text(ended)
                .font(.subheadline)
                .foregroundStyle(scan.scan?.state == "failed" ? AnyShapeStyle(Color.errInk) : AnyShapeStyle(Color.primary))
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - what can be done

    @ViewBuilder
    private func buttons(_ scan: PrintingScanModel) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if scan.scan == nil {
                Button(scan.busy ? PrintingStrings.scanning : PrintingStrings.scanButton) {
                    scan.start()
                }
                .buttonStyle(.borderedProminent)
                .tint(Color.brand)
                .foregroundStyle(Color.brandFg)
                .disabled(!scan.canScan)
            }
            // `can_scan_more` is `state == "waiting"`, computed server-side
            // (`scans.py:98`): the client never decides whether the feeder
            // will take another page.
            if scan.canScanMore {
                Button(PrintingStrings.scanAnotherPage) { scan.scanAnotherPage() }
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
            }
            if scan.canFinish {
                Button(PrintingStrings.doneButton) { scan.finish() }
                    .buttonStyle(.borderedProminent)
                    .tint(Color.brand)
                    .foregroundStyle(Color.brandFg)
            }
            if scan.scan?.finished == false {
                Button(PrintingStrings.cancel) { scan.cancel() }
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
                    .disabled(scan.busy)
            }
        }
    }
}
