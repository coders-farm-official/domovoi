import DomovoiKit
import SwiftUI

/// A draft, as the card that stands in for the reply.
///
/// `DraftCard` (`DraftUi.kt:214`): kind · title · a `vN` pill · `latest vN`,
/// the first lines, the change line, and `open`.
///
/// ### What is not, and why there is no control for it
///
/// * **No Revise box** (`DraftUi.kt:263`). It sends `revise_draft` on a
///   message, and ``ChatPath/sendBody(content:deviceID:)`` writes
///   `{content, device_id}` only — a box that cannot send is not drawn.
///   Hand editing reaches the same place through a route of its own
///   (`POST .../versions`), so the feature is here and the shortcut is not.
/// * **No save, share or print on the card.** Android offers all three
///   (`:258-262`); they are the canvas's, one tap further in, and a card that
///   saved a version a person has not read is a control that does something
///   bigger than it looks. Print is left out everywhere —
///   ``ChatMoreDraftModel`` says why, and it is Android's own behaviour on a
///   house with no printer.
/// * **No `latest vN` on a card that already is the latest** — "latest v3"
///   beside "v3" is furniture, and Android's own `takeIf` says the same
///   (`DraftUi.kt:166`).
struct ChatMoreDraftCardView: View {

    let draft: ChatMoreDraftRef
    let open: () -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            heading
            Text(draft.title)
                .font(.body.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            if !draft.change.isEmpty {
                Text(draft.change)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            Button(ChatMoreStrings.open, action: open)
                .buttonStyle(.plain)
                .font(.subheadline)
                .foregroundStyle(Color.brandInk)
                .frame(minHeight: 44)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.fgMuted.opacity(0.10)))
        // One element, so the card reads as a card: "draft: Boiler service at
        // Fernbrook, version 2" (`DraftUi.kt:241`).
        .accessibilityElement(children: .combine)
        .accessibilityLabel(ChatMoreStrings.draftCard(title: draft.title, version: draft.version))
    }

    /// kind · vN, in a column at an accessibility size (NIGHT-RULES §3).
    @ViewBuilder
    private var heading: some View {
        let words = [ChatMoreStrings.kindLabel(draft.kind), ChatMoreStrings.version(draft.version)]
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(words, id: \.self) { pill($0) }
            }
        } else {
            HStack(spacing: 6) {
                ForEach(words, id: \.self) { pill($0) }
                Spacer(minLength: 0)
            }
        }
    }

    private func pill(_ word: String) -> some View {
        Text(word)
            .font(.caption2)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(RoundedRectangle(cornerRadius: 6).fill(Color.fgMuted.opacity(0.12)))
    }
}

/// The draft itself: its versions, what changed, a hand edit, a save, the
/// file, and read-aloud.
///
/// `DraftCanvas` (`DraftUi.kt:291`), over the conversation rather than in its
/// place: this app's Chat is one screen inside the workspace's
/// `NavigationStack` and a full-screen canvas there would take the whole
/// shell, including the phone bar. A sheet is the iOS form of "the
/// conversation's place".
///
/// ### Three things that are decisions and not layout
///
///  1. **It opens at the version the card named**, not the latest
///     (``ChatMoreDraftRules/opensAt(_:asked:)``): the reply the card sits
///     under is about v2, and v3's words are ones that reply never wrote.
///  2. **The changes switch starts on for a version this phone has not seen**
///     (``ChatMoreDraftRules/changesOn(version:seen:)``) and is not drawn at
///     all for a version that has none — v1 never has any.
///  3. **A 409 is said out loud.** `POST .../versions` refuses an edit based
///     on anything but the newest version, and
///     ``ChatMoreStrings/newerVersionCameIn`` promises the typed words are
///     still there — which they are, because ``ChatMoreDraftModel/saveEdit()``
///     leaves `editing` alone on that one status.
///
/// The editor is a multi-line `TextField(axis: .vertical)` and so carries
/// **no `submitLabel` and no `onSubmit`** (NIGHT-RULES §3): its Return key
/// makes a newline, and a key labelled `save` would lie about that.
struct ChatMoreDraftCanvasView: View {

    let model: ChatMoreDraftModel
    let close: () -> Void

    @State private var exporting = false
    /// The export on its way to the share sheet.
    ///
    /// Presented from **here** and not from the conversation's own
    /// ``ChatMoreSheet``: this canvas is already in a sheet, and swapping that
    /// sheet's item for a share sheet would close the canvas behind it —
    /// Android's share opens over the canvas and comes back to it.
    @State private var sharing: ChatMoreOutgoingFile?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if let why = model.error {
                        Text(why)
                            .font(.subheadline)
                            .foregroundStyle(Color.errInk)
                            .fixedSize(horizontal: false, vertical: true)
                            .multilineTextAlignment(.leading)
                    } else if model.view == nil {
                        Text(ChatMoreStrings.readingTheDraft)
                            .font(.subheadline)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        versions
                        source
                        changesSwitch
                        words(of: model)
                        actions
                        saved
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(model.view?.draft.title ?? ChatMoreStrings.draft)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(ChatMoreStrings.backToTheChat, action: close)
                }
            }
        }
        .sheet(item: $sharing) { outgoing in
            ChatMoreFileShareSheet(name: outgoing.name, bytes: outgoing.bytes)
        }
    }

    // MARK: - the version chips

    private var versions: some View {
        // A wrapping column rather than a horizontal scroller: at an
        // accessibility size six "v6" chips are wider than the screen, and a
        // horizontal scroller inside this vertical one is two gestures over
        // one drag (``ChatMoreRowTop`` says the same).
        VStack(alignment: .leading, spacing: 4) {
            ForEach(model.versions) { version in
                Button {
                    model.select(version.version)
                } label: {
                    HStack(spacing: 6) {
                        Text(ChatMoreStrings.version(version.version))
                            .font(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                        if version.version == model.latest?.version {
                            Text(ChatMoreStrings.latest)
                                .font(.caption2)
                                .foregroundStyle(Color.fgMuted)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 8)
                    .frame(minHeight: 44)
                    .background(
                        RoundedRectangle(cornerRadius: 8).fill(
                            Color.fgMuted.opacity(version.version == model.selected ? 0.22 : 0.08)
                        )
                    )
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.brandInk)
                .accessibilityAddTraits(version.version == model.selected ? [.isSelected] : [])
            }
        }
    }

    @ViewBuilder
    private var source: some View {
        let label = model.sourceLabel
        if !label.isEmpty {
            Text(label)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
    }

    /// Drawn **only** when the version on screen has changes and nothing is
    /// being edited (``ChatMoreDraftModel/changesSwitchLive``) — a switch that
    /// had nothing to show would be a control that does nothing.
    @ViewBuilder
    private var changesSwitch: some View {
        if model.changesSwitchLive {
            Toggle(
                ChatMoreStrings.changes,
                isOn: Binding(get: { model.showChanges }, set: { model.setShowChanges($0) })
            )
            .font(.subheadline)
            .tint(Color.brand)
        }
    }

    // MARK: - the words

    @ViewBuilder
    private func words(of model: ChatMoreDraftModel) -> some View {
        if let editing = model.editing {
            TextField(
                ChatMoreStrings.edit,
                text: Binding(get: { editing }, set: { model.setEditedText($0) }),
                axis: .vertical
            )
            .textFieldStyle(.roundedBorder)
            .lineLimit(6...24)
            .accessibilityLabel(ChatMoreStrings.edit)
        } else if model.showChanges, let changes = model.version?.changes {
            ChatMoreChangesView(changes: changes)
        } else if let version = model.version {
            // `display` is the server's own rendering of the body
            // (`_version_row`, `chat_drafts.py:105`); `body` is the markdown
            // it came from, and the canvas shows what a reader would get.
            Text(version.display.isEmpty ? version.body : version.display)
                .font(.body)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
    }

    // MARK: - what can be done to it

    @ViewBuilder
    private var actions: some View {
        VStack(alignment: .leading, spacing: 4) {
            if model.editing == nil {
                HomeLink(ChatMoreStrings.edit) { model.beginEdit() }
                HomeLink(ChatMoreStrings.saveAs(model.format)) { model.save() }
                HomeLink(ChatMoreStrings.saveAs(model.otherFormat)) {
                    model.save(format: model.otherFormat)
                }
                HomeLink(ChatMoreStrings.share) { exporting = true }
                    .disabled(exporting)
                ChatMoreReadAloudButton(text: model.version?.body ?? "")
            } else {
                HomeLink(ChatMoreStrings.saveAsVersion(model.nextVersion)) { model.saveEdit() }
                HomeLink(ChatMoreStrings.cancel) { model.cancelEdit() }
            }
        }
        // **The export's `await` lives on the canvas, not on a row**
        // (NIGHT-RULES §3). It is a `.task(id:)` rather than a `Task {}` in
        // the button so that a canvas closed mid-export cancels it.
        .task(id: exporting) {
            guard exporting else { return }
            defer { exporting = false }
            guard let file = await model.export() else { return }
            sharing = ChatMoreOutgoingFile(name: file.name, bytes: file.bytes)
        }
    }

    @ViewBuilder
    private var saved: some View {
        if let result = model.saved {
            Text(ChatMoreStrings.savedToast(where: result.whereText, check: result.checkLine))
                .font(.caption)
                .foregroundStyle(result.checkOK ? Color.okInk : Color.warnInk)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
    }
}

/// What changed between this version and the one before it.
///
/// `changesText` (`DraftUi.kt:178`): added words in a wash, removed words
/// struck through — and **never green or red as text**, which is both
/// Android's own note and NIGHT-RULES §3. The wash is `warnInk` at low
/// opacity behind black text; the strike-through is what carries "removed",
/// so the meaning survives a reader who sees no colour at all.
struct ChatMoreChangesView: View {

    let changes: [ChatMoreDraftChange]

    var body: some View {
        Text(marked)
            .font(.body)
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
            // One label, so a screen reader reads the passage rather than
            // forty runs of it. The struck-out words are read out too: a
            // reader listening to "what changed" needs both halves.
            .accessibilityLabel(changes.map(\.text).joined())
    }

    private var marked: AttributedString {
        var out = AttributedString()
        for change in changes {
            var piece = AttributedString(change.text)
            if change.added {
                piece.backgroundColor = Color.warnInk.opacity(0.22)
            } else if change.removed {
                piece.strikethroughStyle = .single
                piece.foregroundColor = Color.fgMuted
            }
            out.append(piece)
        }
        return out
    }
}
