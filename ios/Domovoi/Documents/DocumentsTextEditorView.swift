import DomovoiKit
import SwiftUI
import UIKit

/// Editing one of the household's documents as text — the plain-text editor
/// and the markdown editor, which differ only in the route behind them and in
/// the formatting row.
///
/// `DocumentsEditor.kt:248`'s `TextEditorOverlay`. The state machine, the two
/// routes and every refusal are ``DocumentsEditorModel``'s; this file is the
/// chrome, the typing surface and the eight-button row.
///
/// ### What is not, and why there is no control for it
///
/// * **No autosave**, as Android has none: the save is the button, and
///   closing with edits in hand asks first.
/// * **No Save at all** where this phone may not write (a read-only library,
///   or a device an admin has blocked). ``DocumentsNoSaveNote`` says so in the
///   button's place ([[no-misleading-controls]]).
/// * **No formatted-document or Word editor.** `.html`, `.htm` and `.docx`
///   never reach this view: ``DocumentsKinds/editor(for:in:)`` answers nil for
///   them and their rows keep today's read-only reader, whose
///   "read-only — editing is on the dashboard" line stays true of exactly the
///   files that still reach it. See `overnight/documents/DECISIONS.md`.
/// * **No pictures, co-editing, print or share-as-Word.** They are bridge
///   calls from the dashboard's WebView page, which is not ported.
struct DocumentsTextEditorView: View {

    let entry: FileEntry
    let kind: DocumentsEditorKind
    let files: FilesModel
    let model: ShellModel
    let onClose: () -> Void

    @State private var editor: DocumentsEditorModel?

    /// Where the caret is, in **characters** — what the formatting row needs
    /// and the only thing this view keeps about the typing surface.
    @State private var selection: Range<Int> = 0..<0

    /// A caret the formatting row asked for, with a number that goes up every
    /// time. The number is what tells the typing surface "this is a new
    /// request", so a selection the person moved themselves is never dragged
    /// back ([[no-misleading-controls]] applied to a caret).
    @State private var caret: DocumentsCaretRequest?

    @State private var confirmingDiscard = false

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 0) {
                DocumentsEditorPill(
                    pill: kind.pill,
                    dirty: editor?.dirty ?? false,
                    saving: editor?.saving ?? false
                )
                .padding(.horizontal, 16)
                .padding(.top, 8)

                if let said = editor?.saveFailure {
                    // The server's own sentence, kept on screen rather than
                    // only in a toast: the 409 `empty_over_content` is the one
                    // refusal a person can act on by typing something, and a
                    // toast that had already faded would leave them looking at
                    // an editor that simply would not save.
                    Text(said)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 16)
                        .padding(.top, 6)
                }

                content(of: editor)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .navigationTitle(entry.displayTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                // Both labels are ``DocumentsEditorBarLabel`` and both
                // buttons are `.plain`: the audit failed the disabled `save`
                // on contrast, and the fix is the colour rather than the
                // disabled state. The whole reason is in that view's note.
                ToolbarItem(placement: .cancellationAction) {
                    Button { closeRequested() } label: {
                        DocumentsEditorBarLabel(word: DocumentsStrings.close, live: true)
                    }
                    .buttonStyle(.plain)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if editor?.offersSave == true {
                        let live = editor?.canSave ?? false
                        Button { editor?.save() } label: {
                            DocumentsEditorBarLabel(word: DocumentsStrings.save, live: live)
                        }
                        .buttonStyle(.plain)
                        .disabled(!live)
                    }
                }
            }
        }
        .task {
            if editor == nil {
                let built = DocumentsEditorModel(
                    entry: entry,
                    kind: kind,
                    canWrite: files.canWrite,
                    deviceID: model.deviceID,
                    in: model.container
                )
                // Android's `pager.reload()` after every editor closes
                // (`FilesScreen.kt:794`), done where the save lands instead:
                // a save changes the row's size and time, and the list under
                // this cover is the thing showing them.
                built.onSaved = { files.entries.reload(fresh: true) }
                editor = built
                built.read()
            }
        }
        // The shell's toast, as the rest of Files uses: it is drawn over the
        // whole app, so `saved` survives this cover being dismissed in the
        // same gesture.
        .onChange(of: editor?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            editor?.clearToast()
        }
        .alert(DocumentsStrings.unsavedChangesTitle, isPresented: $confirmingDiscard) {
            Button(DocumentsStrings.discard, role: .destructive) { onClose() }
            Button(DocumentsStrings.keepEditing, role: .cancel) {}
        } message: {
            Text(DocumentsStrings.unsavedChangesBody)
        }
    }

    /// `DocumentsEditor.kt:363-372`: closing with edits in hand asks first,
    /// and only `discard` closes.
    private func closeRequested() {
        if editor?.dirty == true {
            confirmingDiscard = true
        } else {
            onClose()
        }
    }

    // MARK: - the four states

    @ViewBuilder
    private func content(of editor: DocumentsEditorModel?) -> some View {
        switch editor?.load ?? .loading {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityLabel(entry.displayTitle)
        case .ready:
            if let editor {
                ready(editor)
            }
        case .notEditable(let said):
            // The server **answered**: too large, not UTF-8, or an extension
            // this editor does not open. Not a failure, so there is no retry
            // — the one way forward is to take the file onto the phone, and
            // that link is drawn only where it works.
            refusal(said, retry: false)
        case .failed(let why):
            refusal(why, retry: true)
        }
    }

    private func ready(_ editor: DocumentsEditorModel) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            if kind == .markdown {
                DocumentsMarkdownToolbar(
                    enabled: editor.canWrite,
                    tapped: { apply($0, to: editor) }
                )
            }
            DocumentsTextBody(
                text: Binding(get: { editor.text }, set: { editor.text = $0 }),
                editable: editor.canWrite,
                caret: caret,
                named: entry.displayTitle,
                onSelection: { selection = $0 }
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            if !editor.offersSave {
                DocumentsNoSaveNote()
                    .padding(.horizontal, 16)
                    .padding(.bottom, 8)
            }
        }
    }

    private func refusal(_ said: String, retry: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(said)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            if retry {
                HomeLink(FileStrings.retry) { editor?.read() }
            } else if files.offersSave(entry) {
                // `source.rawLabel` under Android's own Unpreviewable state
                // (`DocumentsEditor.kt:336`), offered only where it works.
                HomeLink(FileStrings.verbSave) {
                    onClose()
                    files.saveToDevice(entry)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Apply one formatting tool, and ask the typing surface for the caret the
    /// rule computed.
    ///
    /// The rule itself is ``DocumentsMarkdownTools/apply(_:to:selection:)``,
    /// which is a pure function over character offsets and is tested without
    /// a view — a toolbar that wraps the wrong range is a bug somebody finds
    /// in their own notes.
    private func apply(_ tool: DocumentsMarkdownTool, to editor: DocumentsEditorModel) {
        let edit = DocumentsMarkdownTools.apply(tool, to: editor.text, selection: selection)
        editor.text = edit.text
        selection = edit.selection
        caret = DocumentsCaretRequest(id: (caret?.id ?? 0) + 1, range: edit.selection)
    }
}

// MARK: - the formatting row

/// Android's eight markdown tools (`DocumentsEditor.kt:107-126`).
///
/// It scrolls sideways rather than wrapping: eight buttons at an accessibility
/// type size do not fit across a phone, and a row that wrapped to four lines
/// would push the document itself off the screen. Android hides this row
/// entirely in a window the keyboard has crowded (`:108`); here the row sits
/// above the typing surface and scrolls, which is the same answer to the same
/// problem without measuring the window.
struct DocumentsMarkdownToolbar: View {

    let enabled: Bool
    let tapped: (DocumentsMarkdownTool) -> Void

    /// The button's width grows with the type size. A fixed `frame(width:)`
    /// clipped `H1` at AX5 on the row this rule was written for
    /// (`FileRowView.swift:17-25`).
    @ScaledMetric(relativeTo: .body) private var tool: CGFloat = 44

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 4) {
                ForEach(DocumentsMarkdownTools.all) { one in
                    Button {
                        tapped(one)
                    } label: {
                        Text(one.label)
                            .font(.system(.subheadline, design: .monospaced))
                            .frame(minWidth: tool, minHeight: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(Color.brandInk)
                    // A face reading `B` or `>` announces as a letter; this
                    // is what it does. Android's row has no content
                    // descriptions at all, which is a gap on that side.
                    .accessibilityLabel(one.spoken)
                }
            }
            .padding(.horizontal, 16)
        }
        .scrollIndicators(.hidden)
        .disabled(!enabled)
    }
}

// MARK: - the typing surface

/// A caret the formatting row asked for. The `id` goes up on every request,
/// which is how the surface tells a new one from the one it already honoured.
struct DocumentsCaretRequest: Equatable {
    let id: Int
    /// Character offsets, as ``DocumentsMarkdownEdit`` gives them.
    let range: Range<Int>
}

/// The document itself: a `UITextView` in SwiftUI's clothing.
///
/// **Why not `TextEditor`.** The formatting row needs to know where the
/// selection is and to put it back afterwards, and SwiftUI's `TextEditor`
/// only gained a selection binding in iOS 18 — through `TextSelection`, whose
/// `RangeSet` indices this app cannot compile against on this machine
/// (NIGHT-RULES §5: Swift runs on the Mac, not here). `UITextView` is the API
/// underneath `TextEditor` either way, its `selectedRange` is twenty years
/// old, and the alternative was shipping eight buttons that apply somewhere
/// other than where the caret is — which is the misleading control this
/// screen is built not to draw.
///
/// **No `submitLabel`, no `onSubmit`.** Return makes a newline in a document,
/// so a key labelled `send` or `done` would lie (NIGHT-RULES §3).
struct DocumentsTextBody: UIViewRepresentable {

    @Binding var text: String
    let editable: Bool
    let caret: DocumentsCaretRequest?
    /// The file's name, so VoiceOver announces which document this is rather
    /// than "text view".
    let named: String
    let onSelection: (Range<Int>) -> Void

    /// Monospaced, and **scaled**: `UIFontMetrics` plus
    /// `adjustsFontForContentSizeCategory` is UIKit's Dynamic Type, and a
    /// fixed point size is the defect this track's rules name first.
    static var font: UIFont {
        UIFontMetrics(forTextStyle: .body)
            .scaledFont(for: .monospacedSystemFont(ofSize: 15, weight: .regular))
    }

    func makeUIView(context: Context) -> UITextView {
        let view = UITextView()
        view.delegate = context.coordinator
        view.font = Self.font
        view.adjustsFontForContentSizeCategory = true
        view.backgroundColor = .clear
        view.alwaysBounceVertical = true
        // A document is typed as written: the phone's own corrections would
        // put a capital on a markdown bullet and a smart quote in a code
        // fence, and neither survives being read back on a laptop.
        view.autocorrectionType = .no
        view.autocapitalizationType = .none
        view.smartQuotesType = .no
        view.smartDashesType = .no
        view.spellCheckingType = .no
        view.textContainerInset = UIEdgeInsets(top: 8, left: 12, bottom: 8, right: 12)
        view.accessibilityLabel = named
        view.text = text
        return view
    }

    func updateUIView(_ view: UITextView, context: Context) {
        context.coordinator.text = $text
        context.coordinator.onSelection = onSelection
        if view.text != text {
            view.text = text
        }
        view.isEditable = editable
        // Only a caret this view has not honoured yet. Comparing the number
        // rather than the range is what keeps a second `B` from dragging the
        // selection back to where the first one left it.
        if let caret, context.coordinator.honoured != caret.id {
            context.coordinator.honoured = caret.id
            view.selectedRange = Self.nsRange(caret.range, in: view.text ?? "")
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(text: $text, onSelection: onSelection)
    }

    /// Character offsets → a UTF-16 range, which is the only pair of units
    /// that can disagree here: ``DocumentsMarkdownTools`` counts `Character`s
    /// and `UITextView` counts UTF-16, and an emoji in a note is enough to
    /// make them differ by one.
    static func nsRange(_ range: Range<Int>, in text: String) -> NSRange {
        let count = text.count
        let lower = min(max(range.lowerBound, 0), count)
        let upper = min(max(range.upperBound, lower), count)
        let from = text.index(text.startIndex, offsetBy: lower)
        let to = text.index(text.startIndex, offsetBy: upper)
        return NSRange(from..<to, in: text)
    }

    static func offsets(_ range: NSRange, in text: String) -> Range<Int> {
        guard let found = Range(range, in: text) else {
            let end = text.count
            return end..<end
        }
        let lower = text.distance(from: text.startIndex, to: found.lowerBound)
        let upper = text.distance(from: text.startIndex, to: found.upperBound)
        return lower..<upper
    }

    /// **Deliberately not annotated with an actor.** `UITextViewDelegate` is
    /// isolated to the main actor in the iOS 18 SDK, so these witnesses are
    /// inferred onto it; writing `@MainActor` here as well would be a second
    /// claim about an SDK this machine cannot compile against
    /// (NIGHT-RULES §5), and inference is right either way.
    final class Coordinator: NSObject, UITextViewDelegate {

        var text: Binding<String>
        var onSelection: (Range<Int>) -> Void
        /// The last caret request this coordinator acted on.
        var honoured = 0

        init(text: Binding<String>, onSelection: @escaping (Range<Int>) -> Void) {
            self.text = text
            self.onSelection = onSelection
        }

        func textViewDidChange(_ textView: UITextView) {
            let said = textView.text ?? ""
            text.wrappedValue = said
            onSelection(DocumentsTextBody.offsets(textView.selectedRange, in: said))
        }

        func textViewDidChangeSelection(_ textView: UITextView) {
            onSelection(DocumentsTextBody.offsets(textView.selectedRange, in: textView.text ?? ""))
        }
    }
}
