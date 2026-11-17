import DomovoiKit
import SwiftUI

/// "What does it say?" — a scan's reading as it runs, then the short summary,
/// then the full text on request.
///
/// Android's `ScanReadSheet` (`ui/screens/printing/ScanReadSheet.kt`, commit
/// 54d46869), which is the dashboard's (`web/static/printers.jsx:465-529`).
/// The decisions are ``ScanReadModel``'s; this draws them.
///
/// A sheet of its own, opened from two places as on Android: the Scan sheet
/// after Done (which **closes** for it — ``FilesView`` swaps one sheet for
/// the other, a second sheet over a sheet being the trap that file
/// documents), and a scan row under a printer's recent jobs.
///
/// ### The summary is shown the way a chat reply is
///
/// The handoff says to show `content` with "the same renderer as chat
/// replies", and on this phone a chat reply is plain, selectable `Text`
/// (``ChatBubbleView``). So is this.
///
/// Each part below is its own small view, so no one `body` is a type-checker
/// puzzle.
struct ScanReadSheet: View {

    let scan: PrintingScanJob
    let model: ShellModel
    let onClose: () -> Void

    @State private var reader: ScanReadModel?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if let reader {
                        ScanReadContent(reader: reader)
                    } else {
                        ProgressView()
                            .accessibilityLabel(ScanReadStrings.title)
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(ScanReadStrings.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(PrintingStrings.close) { onClose() }
                }
            }
        }
        .presentationDetents([.large])
        .task {
            // Built here and not in `init`, for ``HomeView``'s reason: the
            // model needs the container.
            if reader == nil {
                reader = ScanReadModel(in: model.container, scan: scan)
            }
            reader?.open()
        }
        // Nothing still in the air lands after the sheet has gone.
        .onDisappear { reader?.close() }
    }
}

/// Everything under the title, in the dashboard's order.
struct ScanReadContent: View {

    let reader: ScanReadModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let name = reader.name, !name.isEmpty {
                Text(name)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let line = reader.progressLine {
                ScanReadProgressRow(line: line)
            }
            if let failed = reader.endedLine {
                ScanReadErrorLine(text: failed)
            }
            if reader.summingUp {
                Text(ScanReadStrings.summingUp)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ScanReadSaysBlock(reader: reader)
            ScanReadFullTextBlock(reader: reader)
            if let trouble = reader.trouble {
                ScanReadErrorLine(text: trouble)
            }
        }
    }
}

/// The reading's progress: a live bar and the words.
struct ScanReadProgressRow: View {

    let line: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(line)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            // Hidden from VoiceOver: the line above says the same, and a
            // second element with the same words is read twice.
            ProgressView()
                .progressViewStyle(.linear)
                .accessibilityHidden(true)
        }
    }
}

/// The summary — or, with no summary, what the server said instead — the
/// AI-off line, and each of the server's notes.
struct ScanReadSaysBlock: View {

    let reader: ScanReadModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let summary = reader.summaryText {
                Text(summary)
                    .font(.body)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            } else if let said = reader.saidText {
                Text(said)
                    .font(.subheadline)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            if reader.showsAiOffNote {
                Text(ScanReadStrings.aiOff)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(reader.notes) { note in
                ScanReadNoteBox(text: note.text)
            }
        }
    }
}

/// One of the server's notes (`scan_pages_guessed`, `scan_pages_unread`,
/// `letter_checked`) in a soft box: Android's `Notice(text, Tone.Idle)`, the
/// neutral wash, with the text in the body colour.
struct ScanReadNoteBox: View {

    let text: String

    var body: some View {
        Text(text)
            .font(.footnote)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.manualWash(.idle))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(Color.fgMuted.opacity(0.25), lineWidth: 1)
            )
    }
}

/// A failure, in the server's words when it sent some.
struct ScanReadErrorLine: View {

    let text: String

    var body: some View {
        Text(text)
            .font(.subheadline)
            .foregroundStyle(Color.errInk)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
    }
}

/// **Show the full text**, then the text itself: monospaced, selectable, in
/// a hairline box. The screen already scrolls, so the box does not.
struct ScanReadFullTextBlock: View {

    let reader: ScanReadModel

    var body: some View {
        if let full = reader.fullText {
            Text(full.text)
                .font(.system(.footnote, design: .monospaced))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color.fgMuted.opacity(0.35), lineWidth: 1)
                )
        } else if reader.offersFullText {
            Button(ScanReadStrings.showFullText) { reader.showFullText() }
                .buttonStyle(PrintingOutlineButtonStyle())
                .disabled(reader.loadingText)
        }
    }
}
