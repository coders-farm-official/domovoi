import DomovoiKit
import SwiftUI

/// Settings → Printers & scanners: what the home server has saved, what it
/// can see on the network, and each printer's status, ink and household
/// defaults.
///
/// `PrintersPanel()` (`PrintersPanel.kt:75-288`). The decisions are all in
/// ``PrintersPanelModel``; this is the drawing.
///
/// ### Everything on this screen is a device-tier request
///
/// Unusually for this track, the port is whole: every control here is a call
/// the phone's device token is allowed to make, there is no phone hardware
/// involved and nothing is uploaded. "Look for printers" is the **home
/// server's** mDNS (`POST /api/printers/discover`,
/// `domovoi/printing/discovery.py:297`) — one HTTP request leaves the phone,
/// nothing opens a socket on the LAN, and no iOS Local Network permission is
/// asked for.
///
/// ### What is not here, and why there is no control for it
///
/// **Rename, Remove, trust-a-certificate, clear-the-job-log, the limits
/// editor.** Each is `require_admin_mutation` at both hops
/// (`web/backend/api/printers.py:185,192,198,320,342`) and would 401 from a
/// phone every time. Android draws none of them either; it prints
/// ``PrintingStrings/adminOnlyLine`` under the list, and so does this —
/// drawn exactly where somebody would go looking for a Rename, which is the
/// shape ``FilesView`` settled for its own missing Delete.
struct PrintersPanelView: View {

    let model: ShellModel

    @State private var panel: PrintersPanelModel?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(PrintingStrings.networkOnlyLine)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if let panel {
                saved(panel)
                foundCard(panel)
            } else {
                ProgressView()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task {
            // Built here and not in `init`, as every other screen's model is:
            // the model needs the container and a `@State` initialiser runs
            // before the view has one.
            if panel == nil {
                panel = PrintersPanelModel(in: model.container)
            }
            panel?.start()
        }
        // The minute re-read stops with the panel. A screen nobody is looking
        // at that keeps asking every printer to re-check itself is a cost the
        // house pays for nothing.
        .onDisappear { panel?.stop() }
        // **The toast is the shell's**, drawn over the whole app, because a
        // toast about a switch that failed must outlive a scroll — the same
        // arrangement ``FilesView`` uses.
        .onChange(of: panel?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            panel?.clearToast()
        }
        .sheet(item: addSheet) { which in
            if let panel {
                PrintingAddView(which: which, panel: panel)
            }
        }
        // An `.alert` and not a sixth sheet: it is a question with two
        // answers, and the sheet above is already presenting whichever add
        // flow asked it.
        .alert(
            PrintingStrings.testPageQuestion,
            isPresented: Binding(
                get: { panel?.offerTestPage != nil },
                set: { if !$0 { panel?.dismissTestPageOffer() } }
            ),
            presenting: panel?.offerTestPage
        ) { printer in
            Button(PrintingStrings.testPageConfirm) { panel?.testPage(printer) }
            Button(PrintingStrings.cancel, role: .cancel) { panel?.dismissTestPageOffer() }
        } message: { printer in
            Text(PrintingStrings.testPageBody(printer.displayTitle))
        }
    }

    // MARK: - the saved list

    @ViewBuilder
    private func saved(_ panel: PrintersPanelModel) -> some View {
        if let trouble = panel.loadError {
            VStack(alignment: .leading, spacing: 8) {
                Text(trouble)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(PrintingStrings.retry) { panel.retry() }
            }
        } else if panel.loading {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                // Android's own word for a read in flight
                // (`PrintersPanel.kt:144`), so VoiceOver has something to say
                // about a spinner rather than announcing "in progress" alone.
                .accessibilityLabel(PrintingStrings.loadingJobs)
        } else if panel.rows.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text(PrintingStrings.noPrintersTitle)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(PrintingStrings.noPrintersBody)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } else {
            ForEach(panel.rows) { printer in
                PrinterCardView(printer: printer, panel: panel)
            }
            Text(PrintingStrings.adminOnlyLine)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - found on the network

    private func foundCard(_ panel: PrintersPanelModel) -> some View {
        PanelCard(title: PrintingStrings.foundTitle, sub: PrintingStrings.foundBody) {
            Button(panel.lookLabel) { panel.look(again: panel.found != nil) }
                .buttonStyle(.bordered)
                .tint(Color.brandInk)
                .disabled(panel.looking)
            Button(PrintingStrings.addByAddress) { panel.addByAddressRequested() }
                .buttonStyle(.bordered)
                .tint(Color.brandInk)
            if panel.looking {
                ProgressView()
                    .progressViewStyle(.linear)
                    .accessibilityLabel(PrintingStrings.looking)
            }
            if let line = panel.nothingToAddLine {
                Text(line)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            // Only the rows the house does **not** already have
            // (`PrintersPanel.kt:189`): offering `Add` for a saved printer
            // would add it twice.
            ForEach(panel.freshlyFound) { row in
                PrintingFoundRow(row: row, panel: panel)
            }
            if let line = panel.diagnosticsLine {
                Text(line)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array(panel.diagnosticsNotes.enumerated()), id: \.offset) { _, note in
                Text(note)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - which add sheet is up

    private var addSheet: Binding<PrintingAddSheet?> {
        Binding(
            get: {
                guard let panel else { return nil }
                if let row = panel.addingFound { return .found(row) }
                if panel.addingByAddress { return .byAddress }
                return nil
            },
            set: { next in
                guard next == nil else { return }
                panel?.cancelAdd()
            }
        )
    }
}

/// One printer the server can see but the house has not saved.
/// `PrintersPanel.kt:198-215`.
struct PrintingFoundRow: View {

    let row: PrintingFound
    let panel: PrintersPanelModel

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 6) { face }
        } else {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) { face }
                Spacer(minLength: 4)
                add
            }
        }
    }

    @ViewBuilder
    private var face: some View {
        // The model, or the address when mDNS gave no model
        // (``PrintingFound/displayTitle``).
        Text(row.displayTitle)
            .font(.subheadline)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
        if !row.location.isEmpty {
            Text(row.location)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        PrintingPillRow(labels: pills)
        if typeSize.isAccessibilitySize { add }
    }

    /// `color` and `duplex` are **genuinely optional** on a found printer —
    /// the server sends null when mDNS did not say (`discovery.py:58-59`), and
    /// a pill drawn for an unknown would claim a capability nobody checked.
    private var pills: [String] {
        var out: [String] = []
        if row.prints { out.append(PrintingStrings.pillPrints) }
        if row.scans { out.append(PrintingStrings.pillScans) }
        if row.color == true { out.append(PrintingStrings.pillColour) }
        if row.duplex == true { out.append(PrintingStrings.pillTwoSided) }
        return out
    }

    private var add: some View {
        Button(PrintingStrings.add) { panel.addRequested(row) }
            .buttonStyle(.bordered)
            .tint(Color.brandInk)
            .disabled(panel.busy != nil)
    }
}
