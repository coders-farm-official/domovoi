import DomovoiKit
import PDFKit
import SwiftUI
import UIKit

/// The one sheet the conversation puts up for everything this item adds.
///
/// One enum and one `.sheet(item:)`, for ``ChatSheet``'s reason: several
/// `.sheet` modifiers on one view do not reliably coexist — the last one
/// attached wins and the others silently never present. Chat already has one
/// sheet of its own on ``ChatView``, which is a different view, so these two
/// do not collide.
enum ChatMoreSheet: Identifiable {
    /// A document passage, with the sentence the reply used marked.
    case source(ChatMoreSource)
    /// Report a reply.
    case report(ChatMoreReport)
    /// A draft, open in the canvas.
    case draft(Int64, Int)
    /// Hold to talk.
    case talk

    /// The case **and** what it is about, so opening a second source replaces
    /// the sheet's content rather than keeping the first one's:
    /// `.sheet(item:)` rebuilds on a changed id and not otherwise.
    var id: String {
        switch self {
        case .source(let source): return "source:\(source.token):\(source.label)"
        case .report(let report): return "report:\(report.answeredAt.timeIntervalSince1970)"
        case .draft(let id, let version): return "draft:\(id):\(version)"
        case .talk: return "talk"
        }
    }
}

/// A file on its way out through the share sheet.
///
/// A type of its own, and a **fresh id every time**, because the two places
/// that hand something over — the canvas's `share` and the report's "share as
/// text" — each present their own nested sheet rather than taking over
/// ``ChatMoreSheet``. Sharing the same draft twice has to put the sheet up
/// twice, and `.sheet(item:)` rebuilds only on a changed id.
///
/// Nested, because they are nested: the canvas and the report sheet are
/// already presented, and a `.sheet` attached to a view inside a sheet is a
/// different view from the one ``ChatMoreSheet`` hangs off. Two `.sheet`
/// modifiers on **one** view are the pair that does not coexist.
struct ChatMoreOutgoingFile: Identifiable {
    let id = UUID()
    let name: String
    let bytes: Data
}

/// Plain text on its way out, likewise.
struct ChatMoreOutgoingText: Identifiable {
    let id = UUID()
    let text: String
}

/// A document passage: the words, the sentence the reply cited, and — for a
/// PDF — the page itself.
///
/// `SourceSheet` (`ChatDocsUi.kt:196`), with one addition that is this port's
/// own. Android marks the `match` inside the passage with a background wash
/// and nothing else; a wash inside an `AttributedString` is invisible to
/// VoiceOver, whose element label is the whole passage either way, so a reader
/// who cannot see the colour would have no way to tell which sentence was
/// cited. ``ChatMoreStrings/theSentenceTheReplyUsed`` is the line above it,
/// and it is drawn **only when the match was actually found in the passage**
/// (``ChatMoreSourceRules/matchRange(in:match:)``) — a heading over nothing
/// would be worse than no heading.
struct ChatMoreSourceSheetView: View {

    let source: ChatMoreSource
    let client: DomovoiClient
    let close: () -> Void

    @State private var page: Data?
    @State private var looked = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text(source.name)
                        .font(.subheadline)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    passage
                    marked
                    pageImage
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(source.label)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(ChatMoreStrings.cancel, action: close)
                }
            }
        }
        // The page is fetched here and not in a row (NIGHT-RULES §3), and
        // only for a kind this phone can render (``ChatMoreSource/rendersPage``).
        .task {
            guard !looked, source.rendersPage else { return }
            looked = true
            page = try? await client.raw(
                method: "GET",
                path: ChatPath.documentFile(source.token),
                trackPairing: false,
                timeout: 10
            )
        }
    }

    private var passage: some View {
        Text(washed)
            .font(.body)
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
            // One label, the whole passage: forty runs read out one at a time
            // is not a passage.
            .accessibilityLabel(source.text)
    }

    /// The passage with the cited sentence washed. `warnInk` at low opacity
    /// behind the text, never a coloured foreground (NIGHT-RULES §3).
    private var washed: AttributedString {
        var out = AttributedString(source.text)
        // The **rule** decides whether there is a match to mark
        // (``ChatMoreSourceRules/matchRange(in:match:)``, which is the ported
        // one and is unit-tested); the `AttributedString` is then asked where
        // it is, because converting a `Range<String.Index>` across the two
        // index spaces is the step that lands a wash on the wrong character
        // of a passage with a combining accent in it.
        guard ChatMoreSourceRules.matchRange(in: source.text, match: source.match) != nil,
              let found = out.range(of: source.match) else { return out }
        out[found].backgroundColor = Color.warnInk.opacity(0.22)
        return out
    }

    @ViewBuilder
    private var marked: some View {
        if ChatMoreSourceRules.matchRange(in: source.text, match: source.match) != nil {
            VStack(alignment: .leading, spacing: 2) {
                Text(ChatMoreStrings.theSentenceTheReplyUsed)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Text(source.match)
                    .font(.callout.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
        }
    }

    /// The page, when the document is a PDF and the bytes arrived.
    ///
    /// **Best effort, and silent when it fails.** The passage above is the
    /// answer; the page is the picture of where it came from, and a sentence
    /// about a PDF that would not parse would be a second thing said about one
    /// citation. Nothing is drawn rather than an empty frame, which is a leaf
    /// the audit has to measure and a person cannot see.
    @ViewBuilder
    private var pageImage: some View {
        if let page, let rendered = ChatMorePdfPage.image(of: page, page: source.page) {
            Image(uiImage: rendered)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(maxWidth: .infinity)
                .accessibilityLabel(ChatMoreStrings.sourceLabel(source.label))
        }
    }
}

/// One page of a PDF, as an image.
///
/// `PageImage` (`ChatDocsUi.kt:238`) renders the page and boxes the passage on
/// it. **The box is not ported**: `PDFSelection` finds a string on a page and
/// `characterBounds(for:)` gives its rectangle, but the server's `match` is
/// the text it extracted — which for a scanned page (`scanned_pages`) is what
/// an OCR pass read and need not appear in the page's own text layer at all.
/// A box drawn where a search happened to land on a page whose words came from
/// somewhere else would point at the wrong sentence with complete confidence,
/// which is worse than not pointing. The marked sentence above the page is
/// where this port says which one it was.
enum ChatMorePdfPage {

    @MainActor
    static func image(of bytes: Data, page number: Int?) -> UIImage? {
        guard let document = PDFDocument(data: bytes), document.pageCount > 0 else { return nil }
        // The server's `page` is 1-based (`chat_documents.py:158`) and
        // PDFKit's index is 0-based; a page number past the end falls back to
        // the first, because a citation into a document this phone was handed
        // a different copy of is not worth drawing nothing for.
        let wanted = max(0, min(document.pageCount - 1, (number ?? 1) - 1))
        guard let page = document.page(at: wanted) else { return nil }
        let box = page.bounds(for: .mediaBox)
        guard box.width > 1, box.height > 1 else { return nil }
        return page.thumbnail(of: CGSize(width: box.width, height: box.height), for: .mediaBox)
    }
}

/// "Report this reply": exactly what would be sent, and the two ways to send
/// it.
///
/// `ReportPane` (`assistant/ReplyReportPanes.kt:69`). The paragraph at the top
/// is the promise the sheet keeps — "the report holds exactly what is below
/// and nothing else; nothing leaves the phone by itself" — so every field of
/// ``ChatMoreReport/asText()`` is on screen above the buttons, and the note is
/// the only thing a person adds.
///
/// ### There is no server
///
/// No route in `web/backend/api/` takes a report; the phone hands it on, which
/// is how Play's policy is satisfied without one. The owner's destination is
/// an email address (`REPORT_DESTINATION`, `ReplyReport.kt:334`), so the two
/// sinks are a `mailto:` draft and the share sheet
/// (``ChatMoreReportSink/available(destination:)``).
///
/// **"Save on this phone" is left out.** Android keeps saved reports under the
/// phone model's own settings and `canSave` is false for a house reply anyway
/// (`ChatScreen.kt:937`).
struct ChatMoreReportSheetView: View {

    let report: ChatMoreReport
    let close: () -> Void

    @Environment(\.openURL) private var openURL

    @State private var note = ""
    @State private var outcome: ChatMoreReportOutcome?
    @State private var sharing: ChatMoreOutgoingText?

    private var filled: ChatMoreReport { report.with(note: note) }

    /// One field of the report, as something `ForEach` can key on.
    ///
    /// ``ChatMoreReport/fields`` is an array of labelled tuples and Swift has
    /// no key path to a tuple component, so `ForEach(fields, id: \.label)`
    /// does not compile. The label is the id: the fields are a fixed set and
    /// no two of them share one.
    private struct Field: Identifiable {
        let id: String
        let text: String
    }

    private var fields: [Field] {
        report.fields.map { Field(id: $0.label, text: $0.text) }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text(ChatMoreStrings.reportExplains)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    ForEach(fields) { field($0.id, $0.text) }
                    // The model line carries no label of its own: Android
                    // draws it as a field (`ReplyReportPanes.kt:89`) and
                    // ``ChatMoreStrings`` has no word for that label, so
                    // rather than invent one the line stands on its own —
                    // "qwen3:8b, on the home server" says what it is.
                    Text(report.modelLine)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    noteField
                    sinks
                    if let said = outcome?.toast {
                        Text(said)
                            .font(.caption)
                            .foregroundStyle(Color.errInk)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(ChatMoreStrings.reportTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(ChatMoreStrings.cancel, action: close)
                }
            }
        }
        .sheet(item: $sharing) { outgoing in
            ChatShareSheet(text: outgoing.text)
        }
    }

    private func field(_ label: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            Text(text)
                .font(.callout)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The note. A multi-line field, so **no `submitLabel` and no `onSubmit`**
    /// (NIGHT-RULES §3): Return makes a newline here, and a key labelled
    /// `send` would be a key that sends nothing.
    private var noteField: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(ChatMoreStrings.reportYourNote)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            TextField(
                ChatMoreStrings.reportNotePlaceholder,
                text: $note,
                // Owner answer 65's vertical leg (`ShellHintPrompt`).
                prompt: ShellHintPrompt.text(ChatMoreStrings.reportNotePlaceholder),
                axis: .vertical
            )
                .textFieldStyle(.roundedBorder)
                .lineLimit(2...6)
                .accessibilityLabel(ChatMoreStrings.reportNotePlaceholder)
                .onChange(of: note) { _, typed in
                    // The server's own cap (`NOTE_MAX_CHARS`,
                    // `ReplyReport.kt:123`), applied where it is typed rather
                    // than silently cutting the report later.
                    if typed.count > ChatMoreReport.noteMaxChars {
                        note = String(typed.prefix(ChatMoreReport.noteMaxChars))
                    }
                }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// A button per sink, and the sentence each one says **before** it is
    /// tapped (`ReportField`, `ReplyReportPanes.kt:125`) — so nobody finds out
    /// what a button does by pressing it.
    private var sinks: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(ChatMoreReportSink.available(), id: \.self) { sink in
                VStack(alignment: .leading, spacing: 2) {
                    HomeLink(sink.label) { hand(to: sink) }
                    Text(sink.explains)
                        .font(.caption2)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func hand(to sink: ChatMoreReportSink) {
        switch sink {
        case .share:
            sharing = ChatMoreOutgoingText(text: filled.asText())
            outcome = .handedOn
        case .email:
            guard let url = filled.mailtoURL() else {
                outcome = .failed
                return
            }
            // `openURL`'s completion says whether anything on this phone took
            // the `mailto:` — a phone with no mail app set up is
            // ``ChatMoreReportOutcome/noApp``, which is the one case Android
            // has a sentence for (`ReplyReport.kt:368`).
            openURL(url) { opened in
                outcome = opened ? .handedOn : .noApp
            }
        }
    }
}

/// A file on its way out through the share sheet.
///
/// The bytes are written to a temporary file because `UIActivityViewController`
/// hands activities a `URL` or a value, and a .docx handed over as `Data` has
/// no name — the name is the whole point of
/// ``ChatMoreDraftRules/exportName(title:version:format:)``. ``FileShareSheet``
/// (`FileViewers.swift:212`) is the sheet; this is the file under it.
struct ChatMoreFileShareSheet: View {

    let name: String
    let bytes: Data

    @State private var url: URL?

    var body: some View {
        Group {
            if let url {
                FileShareSheet(url: url)
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .accessibilityLabel(ChatMoreStrings.share)
            }
        }
        .task {
            guard url == nil else { return }
            let target = FileManager.default.temporaryDirectory
                .appendingPathComponent(name.isEmpty ? "draft" : name)
            guard (try? bytes.write(to: target, options: .atomic)) != nil else { return }
            url = target
        }
    }
}
