import DomovoiKit
import SwiftUI

/// One printer's card: what it is, how it is, what ink it has left, the
/// household's defaults for it, and the last ten things it printed.
///
/// `PrinterCard` (`PrintersPanel.kt:292-462`), with its three small pieces —
/// the status dot, a supply bar and a capability pill — as their own views
/// below rather than nested closures, because each is measured by the
/// accessibility-size case.
///
/// ### What is not here, and why there is no control for it
///
/// **Rename, Remove and "trust this certificate".** All three are
/// `require_admin_mutation` at both hops
/// (`web/backend/api/printers.py:185,192,198`) and this app holds a device
/// token, so each would 401 from a phone every time. Android draws none of
/// them either; the panel prints ``PrintingStrings/adminOnlyLine`` under the
/// list, and ``PrintersPanelView`` does the same. A changed certificate
/// arrives inside ``PrinterStatus/words`` and says what it says — the
/// sentence is shown, and there is no button beside it.
struct PrinterCardView: View {

    let printer: Printer
    let panel: PrintersPanelModel

    @Environment(\.dynamicTypeSize) private var typeSize

    /// Opens the printer's own web page in Safari. Taken from the environment
    /// rather than drawn as a `Link`; see ``actions``.
    @Environment(\.openURL) private var openURL

    /// The dot's diameter, which **grows with the type size**. A flat
    /// `frame(width:)` on a glyph is the mistake `FileRowView` carries a
    /// paragraph about (`FileRowView.swift:32-40`); a status dot beside text
    /// that has doubled is the same fault in miniature.
    @ScaledMetric(relativeTo: .caption) private var dotSize: CGFloat = 9

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            heading
            statusBlock
            supplies
            pills
            // **Only a printer that can print gets the printing half of the
            // card.** Android wraps the Default switch, the room chips, the
            // two dropdowns and the recent-jobs link in `if (p.canPrint)`
            // (`PrintersPanel.kt:340,352,415`). `can_print` is
            // `bool(row.get("ipp_address"))` (`devices.py:241`), so a
            // scan-only eSCL device is a shape the server really produces —
            // and on one of those a "one per house" switch cannot make it the
            // house's print default (`devices.pick_printer` needs an IPP
            // address), the room chips cannot either, and the recent list is
            // forever "nothing printed yet". Three controls that cannot do
            // what they say ([[no-misleading-controls]]). The heading,
            // status, ink and pills stay: those are true of a scanner too.
            if printer.canPrint {
                defaults
            }
            actions
            if printer.canPrint {
                jobs
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - who it is

    private var heading: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(printer.displayTitle)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
            if let sub = printer.displaySubtitle {
                Text(sub)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - how it is

    /// The dot, the state, when it was last checked, and whatever the printer
    /// itself had to say (`PrintersPanel.kt:310-324`).
    ///
    /// Every sentence in here is the **server's**, never this app's: the
    /// `words`, the `message` and `needs_attention` are written by
    /// `devices.py` out of what the printer reported, and rewording them here
    /// would make two houses disagree about the same fault.
    private var statusBlock: some View {
        VStack(alignment: .leading, spacing: 4) {
            // A row at ordinary sizes and a column at an accessibility one:
            // the dot, a state and "· checked 2 min ago" do not fit across a
            // phone when the type has doubled.
            dotAndState
            ForEach(Array(printer.status.words.enumerated()), id: \.offset) { _, word in
                Text(word)
                    .font(.footnote)
                    .foregroundStyle(printer.status.tone == "err" ? AnyShapeStyle(Color.errInk) : AnyShapeStyle(Color.fgMuted))
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !printer.status.message.isEmpty {
                Text(PrintingStrings.printerSays(printer.status.message))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let attention = printer.needsAttention, !attention.isEmpty {
                Text(attention)
                    .font(.footnote)
                    .foregroundStyle(Color.warnInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let job = printer.activeJob {
                // ``PrintingJob/progressLine``, not the raw `progress`:
                // Android is `j.progress.ifBlank { j.state }.lowercase()`
                // (`PrintersPanel.kt:322-324`), and the decoder's own
                // contract defaults `progress` to "" for an older house —
                // which would render "the council letter: " with nothing
                // after the colon.
                Text(PrintingStrings.activeJobLine(job.title, job.progressLine))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private var dotAndState: some View {
        let state = printer.status.state.lowercased()
        // Optional, and drawn only when there is a timestamp: Android draws
        // the line inside `p.status.checkedAt?.let { ... }`
        // (`PrintersPanel.kt:313-315`), `checked_at` is genuinely null for a
        // printer nothing has polled yet (`devices.py:270`), and
        // ``relTime(_:nowMs:)`` answers "—" for a nil one — so a printer
        // added a second ago would read "· checked —".
        let checked = printer.status.checkedAt.map { at in
            PrintingStrings.checked(
                relTime(at, nowMs: Int64(Date().timeIntervalSince1970 * 1000))
            )
        }
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    PrinterStatusDot(
                        tone: printer.status.tone,
                        size: dotSize,
                        live: printer.status.isPrinting
                    )
                    Text(state)
                        .font(.caption)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let checked {
                    Text(checked)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else {
            HStack(spacing: 6) {
                PrinterStatusDot(
                    tone: printer.status.tone,
                    size: dotSize,
                    live: printer.status.isPrinting
                )
                Text(state)
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                if let checked {
                    Text(checked)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - ink

    /// The bars, or the one line that says there are none.
    ///
    /// ``Printer/reportsSupplies`` is `supplies_reported && !supplies.isEmpty`
    /// (`PrintersPanel.kt:327`): a 0% bar for a printer that reports nothing
    /// is a **false reading**, not a cosmetic default, which is the whole of
    /// `testPrinting_InkIsShownOnlyWhereItIsReported`.
    @ViewBuilder
    private var supplies: some View {
        if printer.reportsSupplies {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(printer.supplies) { supply in
                    PrinterSupplyBar(supply: supply)
                }
            }
        } else {
            Text(PrintingStrings.doesNotReportInk)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - what it can do

    private var pills: some View {
        // Wrapping, not a horizontal scroll: four short words at an
        // accessibility size are wider than a phone, and a pill nobody can
        // see is a capability nobody knows about.
        PrintingPillRow(labels: capabilityLabels)
    }

    private var capabilityLabels: [String] {
        var out: [String] = []
        if printer.canPrint { out.append(PrintingStrings.pillPrints) }
        if printer.canScan { out.append(PrintingStrings.pillScans) }
        if printer.color { out.append(PrintingStrings.pillColour) }
        if printer.duplex { out.append(PrintingStrings.pillTwoSided) }
        return out
    }

    // MARK: - the household's defaults

    private var defaults: some View {
        VStack(alignment: .leading, spacing: 10) {
            Toggle(isOn: Binding(
                get: { printer.isDefault },
                set: { panel.setDefault(printer, on: $0) }
            )) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(PrintingStrings.defaultSwitch)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(PrintingStrings.onePerHouse)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .tint(Color.brand)
            .disabled(panel.busy != nil)

            if !panel.rooms.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text(PrintingStrings.defaultInRooms)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    // The rooms are captured by VALUE for the tap. They are
                    // `printers.data?.rooms`, which every list read replaces
                    // — the minute refresh, an add, a retry — so a tap that
                    // landed against a frame drawn before a read that
                    // shortened the list would trap on an out-of-range
                    // subscript and take the app with it.
                    let chips = panel.rooms
                    PrintingPillRow(
                        labels: chips.map(\.chipLabel),
                        on: Set(roomChipsOn),
                        tap: { index in
                            guard index < chips.count else { return }
                            panel.toggleRoom(printer, chips[index])
                        }
                    )
                }
            }

            // **Only where the hardware can do it.** `duplex` and `color` are
            // the printer's own capabilities (`devices.py:239`), and a
            // "two-sided by default" switch on a printer with one paper path
            // is a control that cannot do what it says
            // ([[no-misleading-controls]]).
            if printer.duplex {
                triStatePicker(
                    PrintingStrings.twoSidedByDefault,
                    value: printer.twoSidedDefault,
                    label: printingTwoSidedDefaultLabel
                ) { panel.setTwoSidedDefault(printer, $0) }
            }
            if printer.color {
                triStatePicker(
                    PrintingStrings.colourByDefault,
                    value: printer.colorDefault,
                    label: printingColorDefaultLabel
                ) { panel.setColorDefault(printer, $0) }
            }
        }
    }

    private var roomChipsOn: [Int] {
        panel.rooms.enumerated()
            .filter { printer.rooms.contains($0.element.id) }
            .map(\.offset)
    }

    /// `nil` / `true` / `false`, where `nil` is "follow the printer" and
    /// travels as a **literal JSON null**. ``PrintingIo`` says why that cannot
    /// go through `JSONEncoder`, and
    /// `testPrinting_TheDefaultsThatFollowThePrinterSendALiteralNull` is the
    /// case that would catch it being replaced by one.
    private func triStatePicker(
        _ title: String,
        value: Bool?,
        label: @escaping (Bool?) -> String,
        pick: @escaping (Bool?) -> Void
    ) -> some View {
        PrintingMenuRow(
            title: title,
            chosen: label(value),
            enabled: panel.busy == nil,
            // `[Bool?]` written out: a literal of `nil, true, false` leaves
            // Swift to infer the element type, and the one it must infer here
            // is the optional — which is the whole point of the control.
            options: ([nil, true, false] as [Bool?]).map { option in
                PrintingMenuRow.Option(label: label(option)) { pick(option) }
            }
        )
    }

    // MARK: - the three buttons

    private var actions: some View {
        VStack(alignment: .leading, spacing: 8) {
            if printer.canPrint {
                Button(PrintingStrings.testPage) { panel.testPage(printer) }
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
                    .disabled(panel.busy != nil)
            }
            // `identify` is the server's verdict on whether this printer has
            // the IPP Identify operation at all (`devices.py:239`). Without
            // the gate the button would 409 on every printer that has not.
            if printer.identify {
                Button(PrintingStrings.whichOneIsIt) { panel.identify(printer) }
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
                    .disabled(panel.busy != nil)
            }
            // ``Printer/webPage`` is nil unless `admin_url` is `http(s)` — an
            // `ipp:` URI or a scheme Safari would refuse draws no control at
            // all, rather than one whose tap goes nowhere.
            //
            // **A ``HomeLink``, not a `Link`.** Apple's audit failed this one
            // control twice on train S009 — "Hit area is too small" and
            // "Contrast failed" — against `type=9 label='printer's own page'
            // frame=(30, 122.3, 127.7, 18)`. Eighteen points: the
            // `.frame(minHeight: 44)` that was here *reserved* the height and
            // the `Link` went on hit-testing, and being measured on, its drawn
            // text, which is the half of the fix ``HomeLink`` was written to
            // state (`HomeCardView.swift:112-121` — the 44 **and** the
            // `contentShape`). The frame straddled the navigation bar's lower
            // edge at 137.3, so half of what the audit sampled behind the text
            // was the blurred bar rather than the card.
            //
            // **The bar half of that was never the control's fault.** On S012
            // the 44-point box put this link at `y = 98...142` against a bar at
            // `93.3...137.3` - wholly *behind* it, invisible in the
            // screenshot - and "Contrast failed" came back again. Nothing
            // drawn here can fix a measurement taken through a translucent
            // bar; the audit case now returns the panel to its top before it
            // samples (`printingScrollToTop`), and this control is unchanged.
            //
            // Android is a `TextButton` here, beside two `OutlinedButton`s
            // (`PrintersPanel.kt:406-411`) — a button, not a bare link — so
            // the button is also the faithful port, and it is the same
            // ``HomeLink`` the `recent jobs` line below already uses, which
            // the same audit passed.
            if let page = printer.webPage {
                HomeLink(PrintingStrings.printersOwnPage) { openURL(page) }
            }
        }
    }

    // MARK: - the last ten

    private var jobs: some View {
        VStack(alignment: .leading, spacing: 8) {
            let open = panel.showingJobs.contains(printer.id)
            HomeLink(open ? PrintingStrings.hideRecentJobs : PrintingStrings.recentJobs) {
                panel.toggleJobs(printer)
            }
            if open {
                PrinterJobsList(printerID: printer.id, panel: panel)
            }
        }
    }
}

// MARK: - the dot

/// A printer's tone as a dot: `ok`, `warn`, `err`, and anything else is idle
/// (`PrintersPanel.kt:59-64`).
///
/// A **shape**, so the ink tokens are a fill and not text — the tokens were
/// measured for text on the app's canvas, and a 9-point circle is neither.
/// The state's own word is beside it in ordinary ink, so the colour is never
/// the only thing carrying the meaning.
///
/// `live` is Android's own argument (`StatusDot`, `Kit.kt:63-80`, passed
/// `live = p.status.state == "printing"` at `PrintersPanel.kt:312`): a halo
/// that scales out of the dot while the printer is working. It is dropped
/// under Reduce Motion, where a thing that never stops moving is the exact
/// complaint that setting exists for — and nothing is lost, because the
/// state's word beside it already says "printing".
struct PrinterStatusDot: View {

    let tone: String
    let size: CGFloat
    /// Whether the printer is working right now (``PrinterStatus/isPrinting``).
    var live: Bool = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var pulsing = false

    var body: some View {
        ZStack {
            if live, !reduceMotion {
                Circle()
                    .fill(fill)
                    .frame(width: size, height: size)
                    .scaleEffect(pulsing ? 2.4 : 1)
                    .opacity(pulsing ? 0 : 0.4)
                    .animation(
                        .easeOut(duration: 1.6).repeatForever(autoreverses: false),
                        value: pulsing
                    )
                    .onAppear { pulsing = true }
            }
            Circle()
                .fill(fill)
                .frame(width: size, height: size)
        }
        // The halo is twice the dot and must not push the row's text along:
        // it is drawn over a frame the size of the dot itself.
        .frame(width: size, height: size)
        // Decoration: the state is spelled out in the text beside it, and
        // a dot VoiceOver reads as "image" twice per card is noise.
        .accessibilityHidden(true)
    }

    private var fill: Color {
        switch tone {
        case "ok": return Color.okInk
        case "warn": return Color.warnInk
        case "err": return Color.errInk
        default: return Color.fgMuted
        }
    }
}

// MARK: - a supply bar

/// One cartridge: its name, how full it is, and whether the printer called it
/// low. `SupplyBar` (`PrintersPanel.kt:465-485`).
struct PrinterSupplyBar: View {

    let supply: PrinterSupply

    /// The bar's height, scaled: it sits beside `.caption` text, and a
    /// 6-point bar under doubled type is a line nobody can see.
    @ScaledMetric(relativeTo: .caption) private var barHeight: CGFloat = 6

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(line)
                .font(.caption)
                .foregroundStyle(supply.low ? AnyShapeStyle(Color.warnInk) : AnyShapeStyle(Color.fgMuted))
                .fixedSize(horizontal: false, vertical: true)
            GeometryReader { space in
                ZStack(alignment: .leading) {
                    Capsule().fill(.quaternary)
                    if let fraction = printingSupplyFraction(supply) {
                        Capsule()
                            .fill(swatch)
                            .frame(width: max(2, space.size.width * fraction))
                    }
                }
            }
            .frame(height: barHeight)
        }
        // One element, so VoiceOver reads "black toner 8% · low" rather than
        // a label and then an unlabelled bar.
        .accessibilityElement(children: .combine)
        .accessibilityLabel(line)
    }

    private var line: String {
        let level = printingSupplyFraction(supply)
            .map { "\(Int(($0 * 100).rounded()))%" } ?? PrintingStrings.unknownLevel
        // ``PrinterSupply/id``, not `name`: it is `name.isEmpty ? kind : name`,
        // which is Android's `s.name.ifBlank { s.kind }`
        // (`PrintersPanel.kt:468`). `capabilities.py` can emit a cartridge
        // with no name, and " 8% · low" names nothing — in the row and in its
        // VoiceOver label both.
        return "\(supply.id) \(level)" + (supply.low ? PrintingStrings.lowSuffix : "")
    }

    /// The printer's own colour, when it named one this app can read.
    ///
    /// A cartridge that called itself `"deep cyan"` gets the row's muted ink
    /// instead of a colour invented here — ``printingSupplyColor(_:)`` returns
    /// nil and this is its fallback.
    private var swatch: Color {
        guard let parts = printingSupplyColor(supply.color) else { return Color.fgMuted }
        return Color(red: parts.red, green: parts.green, blue: parts.blue)
    }
}

// MARK: - pills

/// A wrapping row of short words — capabilities, or the rooms a printer is the
/// default in.
///
/// It wraps rather than scrolls because four of them at an accessibility size
/// are wider than a phone, and a `ScrollView(.horizontal)` hides the rest
/// behind a gesture nothing announces. ``PrintingPillRow`` lays out by
/// measuring, not by `LazyHGrid`: the labels are a handful of words and the
/// widths are the type size's, so `ViewThatFits` over two arrangements is
/// enough and costs no layout pass of its own.
struct PrintingPillRow: View {

    let labels: [String]
    /// Which indices are "on", for a row that is a set of toggles.
    var on: Set<Int> = []
    /// Nil for a row of plain labels; non-nil makes each pill a button.
    var tap: ((Int) -> Void)? = nil

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        if typeSize.isAccessibilitySize {
            // One per line once the type is big: three pills across is three
            // truncated words.
            VStack(alignment: .leading, spacing: 6) { pills }
        } else {
            HStack(spacing: 6) { pills }
        }
    }

    @ViewBuilder
    private var pills: some View {
        ForEach(Array(labels.enumerated()), id: \.offset) { index, label in
            if let tap {
                Button { tap(index) } label: {
                    PrintingPill(label: label, on: on.contains(index))
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(on.contains(index) ? [.isSelected] : [])
            } else {
                PrintingPill(label: label, on: false)
            }
        }
    }
}

/// One pill. `Pill` (`PrintersPanel.kt:206-213`).
struct PrintingPill: View {

    let label: String
    let on: Bool

    var body: some View {
        Text(label)
            .font(.caption)
            // `brandInk` and never ``Color/brand``: a pill's label is text.
            .foregroundStyle(on ? AnyShapeStyle(Color.brandInk) : AnyShapeStyle(Color.fgMuted))
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 10)
            .frame(minHeight: 44)
            .background(
                Capsule().fill(on ? Color.brandInk.opacity(0.12) : Color.fgMuted.opacity(0.08))
            )
    }
}

// MARK: - a labelled menu

/// A label, the chosen value, and a menu of the others — the shape Android's
/// `DropdownField` has (`PrintersPanel.kt:365-384`, `PrintSheet.kt:186-216`).
///
/// A `Menu` rather than a `Picker`: a `Picker` in a plain `VStack` renders as
/// a segmented control or a wheel depending on the style in force, and both
/// are wrong for three options whose labels are sentences at an accessibility
/// size.
struct PrintingMenuRow: View {

    struct Option: Identifiable {
        let label: String
        let pick: () -> Void
        var id: String { label }
    }

    let title: String
    let chosen: String
    var enabled: Bool = true
    let options: [Option]

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            Menu {
                ForEach(options) { option in
                    Button(option.label) { option.pick() }
                }
            } label: {
                HStack(spacing: 8) {
                    Text(chosen)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.down")
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                }
                .padding(.horizontal, 12)
                .frame(minHeight: 44)
                .frame(maxWidth: .infinity, alignment: .leading)
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.fgMuted.opacity(0.35), lineWidth: 1)
                )
                .contentShape(.rect)
            }
            .disabled(!enabled)
            // The title is the label and the value is the value, so VoiceOver
            // says "two-sided by default, follow the printer" rather than
            // reading a bare sentence out of a card full of them.
            .accessibilityLabel(title)
            .accessibilityValue(chosen)
        }
        // **No row-to-column swap here.** The label is already above the
        // control rather than beside it, so an accessibility size has
        // nothing to rearrange — the chosen value wraps inside the box
        // (`fixedSize(vertical:)`) and the box grows with it.
    }
}

// MARK: - the recent list

/// The last ten jobs for one printer, with a cancel beside the ones the
/// **server** says can be cancelled. `PrintersPanel.kt:415-459`.
///
/// `can_cancel` is computed server-side (`jobs.py:117`) and read, never
/// derived: a cancel offered on a job that has finished is a button that
/// 409s, and a job that could be cancelled and has no button is worse.
struct PrinterJobsList: View {

    let printerID: Int
    let panel: PrintersPanelModel

    var body: some View {
        let rows = panel.jobs[printerID]
        if rows == nil {
            Text(PrintingStrings.loadingJobs)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
        } else if rows?.isEmpty == true {
            Text(PrintingStrings.nothingPrintedYet)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(rows ?? []) { job in
                    PrinterJobRow(job: job, printerID: printerID, panel: panel)
                }
            }
        }
    }
}

struct PrinterJobRow: View {

    let job: PrintingJob
    let printerID: Int
    let panel: PrintersPanelModel

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // A row that becomes a column once the type is big: a title, a state
        // line and a `cancel` do not fit across a phone at AX5.
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) {
                spoken
                cancel
            }
        } else {
            HStack(alignment: .top, spacing: 10) {
                spoken
                Spacer(minLength: 4)
                cancel
            }
        }
    }

    /// The job's three lines as **one** accessibility element.
    ///
    /// Two reasons, and the audit is the second of them. A job is one thing —
    /// VoiceOver saying "scan.pdf", then "done · a simulator · 12m ago" as a
    /// separate stop is three stops for one row, and the `cancel` beside it
    /// stays its own element so it is still reachable.
    ///
    /// The audit's: on train S009 Apple failed `type=48 label='scan.pdf'`
    /// with "The accessibilityLabel of this element is not human-readable" —
    /// a bare filename, which is a label its check reads as an identifier.
    /// **The title is not changed to please it**: it is the house's own
    /// `title` for the job (`jobs.py`), and a title the server sends is not
    /// ours to reword. Combined, the row reads "scan.pdf, done · a simulator ·
    /// 12m ago", which is a sentence — and the same combine is what the two
    /// job rows beside it (`the council letter`, `a spreadsheet.xlsx`) would
    /// have wanted anyway.
    private var spoken: some View {
        VStack(alignment: .leading, spacing: typeSize.isAccessibilitySize ? 4 : 2) {
            face
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var face: some View {
        Text(job.title.isEmpty ? PrintingStrings.untitledJob : job.title)
            .font(.subheadline)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
        Text(meta)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
        if let said = job.error, !said.isEmpty {
            Text(said)
                .font(.caption)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        // The `cancel` is **not** here: it is a sibling of ``spoken`` in both
        // layouts, because a button swallowed by `children: .combine` is a
        // button nothing can reach.
    }

    /// `state · who · 10 min ago` (`PrintersPanel.kt:432-437`).
    ///
    /// An **unfinished** job shows its progress sentence, not its bare state:
    /// Android is `(if (j.finished) j.state else j.progress.ifBlank { j.state }).lowercase()`,
    /// and the server already sends `progress` as a finished sentence —
    /// "printing · 3 of 6" (`domovoi/printing/jobs.py:107-109`). Dropping it
    /// loses the one number a person wants from a job still going.
    private var meta: String {
        let age = relTime(job.createdAt, nowMs: Int64(Date().timeIntervalSince1970 * 1000))
        let word = job.finished ? job.state.lowercased() : job.progressLine
        return [word, job.who, age]
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    @ViewBuilder
    private var cancel: some View {
        if job.canCancel, !job.finished {
            Button(PrintingStrings.cancel) { panel.cancelJob(job, on: printerID) }
                .buttonStyle(.bordered)
                .tint(Color.brandInk)
        }
    }
}
