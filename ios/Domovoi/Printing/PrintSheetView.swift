import DomovoiKit
import SwiftUI

/// Printing one of the home server's files: the options that can change
/// something, the preview the server draws of them, and then the job itself
/// to the end.
///
/// `PrintSheet()` (`PrintSheet.kt:59-244`). Every decision is
/// ``PrintingJobModel``'s — which controls exist, whether Print may be
/// pressed, whether a preview that just landed still belongs to the printer
/// on screen.
///
/// ### The phone never reads the file
///
/// `PrintBody` is `library_id` + `path` (`printers.py:228-233`); the web
/// process reads the bytes itself through the Files tab's containment and
/// posts them to the core. So this sheet sends one ordinary JSON body, and
/// there is no file picker, no multipart writer and nothing on the phone to
/// hold a 120 MB PDF.
///
/// ### What is not here, and why there is no control for it
///
/// **The phone's own AirPrint.** This app has no phone-local files at all;
/// Android keeps its print dialog for the phone half of its Files screen
/// (`PrintSheet.kt:54-55`) and there is no phone half here, so there would be
/// nothing for an AirPrint sheet to print.
///
/// **A resolution or quality picker.** Neither platform asks; the five keys
/// the server keeps are `pages`, `copies`, `sides`, `color` and `media`
/// (`printers.py:271-272`) and anything else is dropped silently, so a sixth
/// control would be a setting that went nowhere.
///
/// **Closing this sheet does not cancel the print.** A job the server has
/// accepted is the household's, and walking away from a sheet is not
/// cancelling it — unlike a scan, which holds the scanner and has pages
/// nothing has saved (``ScanSheetView``). `cancel` is beside the job while
/// the server says it can be cancelled.
struct PrintSheetView: View {

    let entry: FileEntry
    let libraryID: String
    let printers: PrintersResponse
    let model: ShellModel
    let onClose: () -> Void

    @State private var job: PrintingJobModel?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let job {
                        if job.job == nil {
                            options(job)
                        } else {
                            PrintingJobProgress(job: job)
                        }
                    } else {
                        ProgressView()
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(job?.title ?? PrintingStrings.printTitle(entry.displayTitle))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(job?.endMessage == nil ? PrintingStrings.cancel : PrintingStrings.close) {
                        onClose()
                    }
                }
            }
        }
        .task {
            if job == nil {
                job = PrintingJobModel(
                    in: model.container,
                    target: .file(libraryID: libraryID, path: entry.rel),
                    fileName: entry.displayTitle,
                    printers: printers
                )
                job?.start()
            }
        }
        .onDisappear { job?.stop() }
        .onChange(of: job?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            job?.clearToast()
        }
    }

    // MARK: - the options

    @ViewBuilder
    private func options(_ job: PrintingJobModel) -> some View {
        let controls = job.controls
        if let preview = job.preview, !preview.thumbs.isEmpty {
            PrintingThumbStrip(thumbs: preview.thumbs)
        }
        // The printer's name as a **line** when there is no picker to show it
        // in (`PrintSheet.kt:163-165`): one printer is not a choice, and a
        // picker with one option is a control that cannot change anything.
        if let line = job.printerLine {
            Text(line)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
        }
        if controls.printer {
            PrintingMenuRow(
                title: PrintingStrings.printerLabel,
                chosen: job.printer?.displayTitle ?? "",
                options: job.printers.map { printer in
                    PrintingMenuRow.Option(label: printer.displayTitle) {
                        job.pick(printer: printer)
                    }
                }
            )
        }
        if controls.pages {
            VStack(alignment: .leading, spacing: 4) {
                Text(PrintingStrings.pagesLabel)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                // **Single line, so the Return key tells the truth.** A
                // `TextField(axis: .vertical)` makes a newline on Return and
                // may carry neither `submitLabel` nor `onSubmit`; a page range
                // is `1-3, 5` and never two lines.
                TextField(
                    PrintingStrings.pagesPlaceholder,
                    text: Binding(get: { job.pagesText }, set: { job.typedPages($0) })
                )
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.numbersAndPunctuation)
                .submitLabel(.done)
                .accessibilityLabel(PrintingStrings.pagesLabel)
            }
        }
        if controls.copies {
            PrintingCopiesRow(job: job)
        }
        if controls.sides, let list = job.choices?.sides {
            PrintingMenuRow(
                title: PrintingStrings.twoSidedLabel,
                chosen: printingSidesLabel(job.chosenSides ?? ""),
                options: list.map { value in
                    PrintingMenuRow.Option(label: printingSidesLabel(value)) {
                        job.pick(sides: value)
                    }
                }
            )
        }
        if controls.color, let list = job.choices?.color {
            PrintingMenuRow(
                title: PrintingStrings.colourLabel,
                chosen: printingColorLabel(job.chosenColor ?? ""),
                options: list.map { value in
                    PrintingMenuRow.Option(label: printingColorLabel(value)) {
                        job.pick(color: value)
                    }
                }
            )
        }
        if controls.media, let list = job.choices?.media {
            PrintingMenuRow(
                title: PrintingStrings.paperLabel,
                chosen: job.chosenMedia?.displayLabel ?? "",
                options: list.map { paper in
                    PrintingMenuRow.Option(label: paper.displayLabel) {
                        job.pick(media: paper.id)
                    }
                }
            )
        }
        sentences(job)
        HStack(spacing: 10) {
            Button(job.sending ? PrintingStrings.sending : PrintingStrings.printButton) {
                job.submit()
            }
            .buttonStyle(.borderedProminent)
            .tint(Color.brand)
            .foregroundStyle(Color.brandFg)
            .disabled(!job.canPrint)
            if job.previewing {
                ProgressView()
                    .accessibilityLabel(PrintingStrings.loadingJobs)
            }
        }
    }

    /// `summary`, `notes`, `adjustments`, `over_limit` and the preview's own
    /// failure — **all of them the server's sentences**, shown as they
    /// arrived. `over_limit` is a refusal the household's limits have already
    /// decided, which is why Print is disabled above rather than pressed and
    /// refused.
    @ViewBuilder
    private func sentences(_ job: PrintingJobModel) -> some View {
        if let preview = job.preview {
            if !preview.summary.isEmpty {
                Text(preview.summary)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array(preview.notes.enumerated()), id: \.offset) { _, note in
                Text(note)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array(preview.adjustments.enumerated()), id: \.offset) { _, said in
                Text(said)
                    .font(.caption)
                    .foregroundStyle(Color.warnInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let over = preview.overLimit {
                Text(over)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        if let trouble = job.previewError {
            Text(trouble)
                .font(.footnote)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// `copies` with a `−` and a `+`, bounded by the printer's own `copies_max`
/// (`PrintSheet.kt:177-185`).
///
/// Buttons and not a `Stepper`: a `Stepper`'s own label is the row, and this
/// row's number has to be readable as text by a test and by VoiceOver.
struct PrintingCopiesRow: View {

    let job: PrintingJobModel

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(PrintingStrings.copiesLabel)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 12) {
                // Android's own characters as the visible label
                // (`PrintSheet.kt:181,183`), with a spoken label beside each:
                // `−` read aloud as "minus" is not what the control does.
                Button(PrintingStrings.fewerCopiesGlyph) { job.fewerCopies() }
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
                    .frame(minWidth: 44, minHeight: 44)
                    .disabled(job.copies <= 1)
                    .accessibilityLabel(PrintingStrings.fewerCopies)

                Text("\(job.copies)")
                    .font(.subheadline.monospacedDigit())
                    .accessibilityLabel(PrintingStrings.copiesLabel)
                    .accessibilityValue("\(job.copies)")

                Button(PrintingStrings.moreCopiesGlyph) { job.moreCopies() }
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
                    .frame(minWidth: 44, minHeight: 44)
                    .disabled(job.copies >= job.copiesMax)
                    .accessibilityLabel(PrintingStrings.moreCopies)
            }
        }
    }
}

/// The job this sheet is following: its line, a bar, a cancel while the
/// server says it can be cancelled, and the sentence it ends on.
/// `JobProgress` (`PrintSheet.kt:248-275`).
struct PrintingJobProgress: View {

    let job: PrintingJobModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let said = job.job?.progressLine {
                Text(said)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if job.endMessage == nil {
                // Determinate only while a printing job has finished a page:
                // a bar that claimed a fraction of a queued job would be
                // making one up (`PrintSheet.kt:263-270`).
                if let fraction = job.jobFraction {
                    ProgressView(value: fraction)
                        .progressViewStyle(.linear)
                        .accessibilityLabel(PrintingStrings.printButton)
                } else {
                    ProgressView()
                        .progressViewStyle(.linear)
                        .accessibilityLabel(PrintingStrings.printButton)
                }
            }
            if let ended = job.endMessage {
                Text(ended)
                    .font(.subheadline)
                    .foregroundStyle(job.job?.state == "failed" ? AnyShapeStyle(Color.errInk) : AnyShapeStyle(Color.primary))
                    .fixedSize(horizontal: false, vertical: true)
            }
            if job.job?.canCancel == true, job.job?.finished == false {
                Button(PrintingStrings.cancel) { job.cancelJob() }
                    .buttonStyle(.bordered)
                    .tint(Color.brandInk)
            }
        }
    }
}

/// The pages the server drew, as pictures.
///
/// **Nothing is fetched.** `thumbs[].src` is always a
/// `data:image/png;base64,…` (`render_worker.py:179`), and
/// ``printingThumbData(_:)`` refuses anything else — a strip that quietly made
/// HTTP requests to whatever a printer's render worker put in a field would
/// be a fetch nobody asked for.
struct PrintingThumbStrip: View {

    let thumbs: [PrintingPageThumb]

    /// The thumbnail's height, scaled: it is a picture of a page beside text,
    /// and a 90-point thumb under doubled type is a stamp.
    @ScaledMetric(relativeTo: .body) private var thumbHeight: CGFloat = 90

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 8) {
                ForEach(thumbs) { thumb in
                    if let data = printingThumbData(thumb.src), let image = UIImage(data: data) {
                        VStack(spacing: 2) {
                            Image(uiImage: image)
                                .resizable()
                                .aspectRatio(contentMode: .fit)
                                .frame(height: thumbHeight)
                                .background(Color(.secondarySystemBackground))
                            // The page number under the thumb, as Android
                            // draws it (`PrintSheet.kt:302`).
                            Text("\(thumb.page)")
                                .font(.caption2)
                                .foregroundStyle(Color.fgMuted)
                        }
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel(PrintingStrings.pageNumber(thumb.page))
                    }
                }
            }
        }
        .scrollIndicators(.hidden)
    }
}
