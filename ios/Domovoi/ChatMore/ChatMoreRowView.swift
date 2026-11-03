import DomovoiKit
import SwiftUI
import UIKit

/// What a conversation row gains once documents, pictures, drafts, cards and
/// voice are drawn: the three pieces ``ChatBubbleView`` hooks in, and nothing
/// else.
///
/// `MessageBubble` (`ChatScreen.kt:1364`) draws all of this inline over about
/// two hundred lines. It is three views here because ``ChatBubbleView`` is one
/// of the four files this item may touch and must be left as a hook: a
/// picture row above the words (`:1395`), a card **in place of** the words
/// (`:984`, `:1008`, `:1010`), and the reply's sources, speaker marker and
/// "carry on" below them (`:1410`, `:1449`, `:1517`).
///
/// ### What is not, and why there is no control for it
///
/// * **No paperclip and no staged attachment list.** `ChatModel/send(_:)`
///   takes a `String` and `ChatPath.sendBody(content:deviceID:)` writes
///   `{content, device_id}` — no `images` and no `documents` key — and both
///   are files this item must not edit (NIGHT-RULES §2). An attach button
///   whose message can never carry the attachment is exactly what
///   [[no-misleading-controls]] forbids, so what is drawn is the pictures and
///   documents a row **already has**. DECISIONS.md shared change 1.
/// * **No details sheet.** Android's `•••` also opens the per-reply figures
///   (`detailRows`, `ChatMessageActions.kt:120`); ``ChatStats`` keeps two of
///   its twenty-three fields (`ChatModels.swift:180`), so a details sheet
///   here would be a sheet with two rows in it and a doc comment explaining
///   the other twenty-one.
struct ChatMoreRowActions {

    /// Open a document passage in the page sheet.
    let source: (ChatMoreSource) -> Void
    /// Open a draft in the canvas, at the version the card names.
    let draft: (Int64, Int) -> Void
    /// Report a reply.
    let report: (Int64) -> Void
}

extension ChatMoreRowExtras {

    /// Whether this row's words are replaced by something else.
    ///
    /// A lookup card and a heads-up card **are** the reply (`ChatScreen.kt:984`,
    /// `:1449`), and a draft card takes the reply's text too (`:1010`) — in
    /// all three cases the row's `content` is the server's own wording of the
    /// card, which the card then says better.
    var replacesWords: Bool { isCard || draft != nil }
}

// MARK: - above the words

/// A row's pictures and the documents it carries.
///
/// `ChatScreen.kt:1395-1408` (up to four 84dp thumbnails) and `:1410` (the
/// document marker). A **user** row that is only a document draws no bubble
/// at all (`:1411`), which falls out of ``ChatMoreRowExtras/documentOnly``
/// and an empty `content`.
struct ChatMoreRowTop: View {

    let message: ChatLiveMessage
    let more: ChatMoreModel?

    @ScaledMetric(relativeTo: .body) private var thumb: CGFloat = 84

    private var extras: ChatMoreRowExtras? {
        guard let id = message.id else { return nil }
        return more?.extras(for: id)
    }

    var body: some View {
        if let extras, let more {
            VStack(alignment: .leading, spacing: 6) {
                if !extras.images.isEmpty {
                    pictures(extras.images, store: more.pictures)
                }
                ForEach(Array(extras.documents.enumerated()), id: \.offset) { _, document in
                    ChatMoreDocMarkerView(document: document)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// The thumbnails, wrapped rather than in a row.
    ///
    /// A `ScrollView(.horizontal)` is what Android uses, and it is wrong here:
    /// at an accessibility type size one 84-point thumbnail is 230 points
    /// wide, so four of them would be a horizontal scroller inside a vertical
    /// one — two gestures fighting over the same drag, which is the Compose
    /// overscroll problem `VoiceView.kt:137` works around, arriving from the
    /// other direction. Two per line wraps instead.
    @ViewBuilder
    private func pictures(_ images: [ChatImageRef], store: ChatMorePictureStore) -> some View {
        let rows = stride(from: 0, to: images.count, by: 2).map {
            Array(images[$0..<min($0 + 2, images.count)])
        }
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(rows.enumerated()), id: \.offset) { _, pair in
                HStack(spacing: 6) {
                    ForEach(pair, id: \.token) { image in
                        ChatMorePictureThumbView(image: image, store: store, side: thumb)
                    }
                }
            }
        }
    }
}

/// One thumbnail.
///
/// **Not `AsyncImage`.** `GET /api/chat/uploads/{token}` is READ tier and
/// needs `X-Device-Token`, which `AsyncImage` cannot send; the server also
/// offers `?device_token=`, which would put a credential in a URL and this
/// track does not do that. ``VideoPosterTile`` (`VideoPosterTile.swift:7-9`)
/// settled the same question the same way, and ``ChatMorePictureStore`` is
/// ``VideoPosterStore`` in miniature.
///
/// **The row holds no `Task`** (NIGHT-RULES §3): `want(_:)` is a call into a
/// store that outlives the bubble, and the fetch belongs to it.
struct ChatMorePictureThumbView: View {

    let image: ChatImageRef
    let store: ChatMorePictureStore
    let side: CGFloat

    var body: some View {
        content
            .frame(width: side, height: side)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .onAppear { store.want(image.token) }
    }

    /// Each of the three states carries the name, and the name **alone** is
    /// only on the picture itself.
    ///
    /// The label used to sit on the frame, above this `switch`. It read the
    /// same whether the bytes had arrived, were still coming or had been
    /// refused -- so nothing on screen, and nothing a test could ask, told a
    /// drawn picture from a spinner that was never going to stop. The three
    /// states are three element kinds with three labels now: a picture, a
    /// spinner that says which picture it is waiting for, and a line that
    /// says that one did not load.
    @ViewBuilder
    private var content: some View {
        switch store.picture(image.token) {
        case .image(let bytes):
            if let picture = UIImage(data: bytes) {
                Image(uiImage: picture)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .accessibilityLabel(ChatMoreStrings.pictureNamed(image.name))
            } else {
                // Bytes that are not a picture this phone can open. The store
                // only checks the size, so this is the other half of the same
                // answer, and it says so rather than leaving an empty box.
                missing
            }
        case .loading, nil:
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color.fgMuted.opacity(0.08))
                .accessibilityLabel(ChatMoreStrings.pictureNamed(image.name))
        case .absent, .failed:
            missing
        }
    }

    private var missing: some View {
        Text(ChatMoreStrings.pictureDidntLoad)
            .accessibilityLabel(
                ChatMoreStrings.pictureNamed(image.name)
                    + " \u{2014} " + ChatMoreStrings.pictureDidntLoad
            )
            .font(.caption2)
            .foregroundStyle(Color.fgMuted)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .padding(4)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color.fgMuted.opacity(0.08))
    }
}

/// "attached lease.pdf — a tenancy agreement at Fernbrook", and the notes
/// under it.
///
/// `DocMarker` (`ChatDocsUi.kt:147`). The summary is what is drawn and the
/// file name is only the fallback (``ChatMoreDocument/markerSummary``,
/// `ChatScreen.kt:1410`).
struct ChatMoreDocMarkerView: View {

    let document: ChatMoreDocument

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(ChatMoreStrings.attached(document.markerSummary))
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            ForEach(ChatMoreSourceRules.noteLines(document.notes), id: \.self) { note in
                Text(note)
                    .font(.caption2)
                    .foregroundStyle(Color.warnInk)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - in place of the words

/// The three things that are the reply rather than sitting under it.
///
/// Drawn only when ``ChatMoreRowExtras/replacesWords`` is true, which is the
/// gate ``ChatBubbleView`` applies to its own `words`.
struct ChatMoreRowCards: View {

    let message: ChatLiveMessage
    let more: ChatMoreModel
    let isLastRow: Bool
    let actions: ChatMoreRowActions

    var body: some View {
        if let id = message.id, let extras = more.extras(for: id) {
            VStack(alignment: .leading, spacing: 8) {
                if more.lookupCard(for: id) != nil {
                    ChatMoreLookupCardView(message: id, more: more)
                }
                if let heads = extras.headsUp {
                    ChatMoreHeadsUpCardView(message: id, card: heads, more: more, isLastRow: isLastRow)
                }
                if let draft = extras.draft {
                    ChatMoreDraftCardView(draft: draft) { actions.draft(draft.id, draft.version) }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

// MARK: - under the words

/// The reply's notes and source chips, the speaker marker, "carry on", and
/// the one menu item this row has.
struct ChatMoreRowBottom: View {

    let message: ChatLiveMessage
    let more: ChatMoreModel?
    let isLastRow: Bool
    let actions: ChatMoreRowActions

    private var extras: ChatMoreRowExtras? {
        guard let id = message.id else { return nil }
        return more?.extras(for: id)
    }

    var body: some View {
        if let more, let id = message.id {
            VStack(alignment: .leading, spacing: 6) {
                // **Not under a heads-up card.** `DocPlan.doc_json` puts the
                // checked document's own notes on a heads-up reply as well
                // (`chat_documents.py:521-525`), so for the note kinds
                // (`scanned`, `blurry`, `handwriting`) this restated in
                // `warnInk` what the card above had just said. Android gates
                // it the same way: `if (!m.pending && m.doc?.mode !=
                // "heads_up")` (`ChatScreen.kt`, the reply branch of
                // `MessageBubble`).
                if let doc = extras?.doc, doc.mode != "heads_up",
                   ChatMoreSourceRules.hasAnything(notes: doc.notes, sources: doc.sources) {
                    ChatMoreReplyDocsView(doc: doc, open: actions.source)
                }
                if let mark = more.speakerMark(for: id) {
                    ChatMoreSpeakerMarkView(mark: mark)
                }
                if let carry = more.carryOn(for: id) {
                    HomeLink(ChatMoreJobRules.carryOnText(carry.left)) {
                        more.resumeJob(carry.job.id)
                    }
                    .disabled(more.answering)
                }
                // ``ChatMoreReport/canReport(role:pending:content:)`` and not
                // ``ChatMoreModel/report(for:appVersion:)``: the gate is the
                // same one (`canReport`, `ChatScreen.kt:1347`), and building
                // the whole report — two ISO 8601 formatters — on every
                // redraw of every row to find out whether to draw a glyph is
                // work for a tap that may never come.
                if let extras,
                   ChatMoreReport.canReport(
                       role: extras.role, pending: message.pending, content: extras.content
                   ) {
                    menu(id)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// The row's `•••`.
    ///
    /// Android keeps report-a-message in the row's own overflow menu
    /// (`ChatScreen.kt:1507`) and this is that menu, with the one item this
    /// app has for it. A `HomeLink` under every reply was the alternative and
    /// is worse: "report this reply" is a thing a person does once a year, and
    /// a word under every answer in the house is furniture.
    private func menu(_ id: Int64) -> some View {
        Menu {
            Button(ChatMoreStrings.reportThisReply) { actions.report(id) }
        } label: {
            Image(systemName: "ellipsis")
                .font(.caption)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .foregroundStyle(Color.fgMuted)
        .accessibilityLabel(ChatMoreStrings.more)
        // The reply this menu belongs to, so two replies' menus are told
        // apart by a query — ``openChatRowMenu`` had to do the same thing for
        // the thread list's rows.
        .accessibilityValue(String(id))
    }
}

/// `ReplyDocs` (`ChatDocsUi.kt:167`): the notes, then one chip per page.
struct ChatMoreReplyDocsView: View {

    let doc: ChatMoreDocReply
    let open: (ChatMoreSource) -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(ChatMoreSourceRules.noteLines(doc.notes), id: \.self) { note in
                Text(note)
                    .font(.caption2)
                    .foregroundStyle(Color.warnInk)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            let chips = ChatMoreSourceRules.ordered(doc.sources)
            if !chips.isEmpty {
                Text(ChatMoreStrings.sources)
                    .font(.caption2)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                // **A column at an accessibility size**, a row otherwise
                // (NIGHT-RULES §3): "p. 12 · read" at AX5 is wider than the
                // screen, and three of them side by side cannot be anything
                // else.
                if typeSize.isAccessibilitySize {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(chips, id: \.label) { chip(for: $0) }
                    }
                } else {
                    HStack(spacing: 6) {
                        ForEach(chips, id: \.label) { chip(for: $0) }
                        Spacer(minLength: 0)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func chip(for source: ChatMoreSource) -> some View {
        Button {
            open(source)
        } label: {
            Text(ChatMoreSourceRules.chipText(source))
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 8)
                .frame(minHeight: 44)
                .background(
                    RoundedRectangle(cornerRadius: 8).fill(Color.fgMuted.opacity(0.12))
                )
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        // The chip's words are the page; the sentence says whether the reply
        // cited it or only read it (`ChatDocsUi.kt:188`).
        .accessibilityLabel(ChatMoreSourceRules.chipText(source))
        .accessibilityHint(ChatMoreSourceRules.chipDescription(source))
    }
}

/// `SpeakerMarker` (`SpeakerMarker.kt:69`): who Domovoi heard.
///
/// `soft` is a weaker ink and not a different word — the word is already
/// "maybe Ana" — and the screen-reader sentence is the marker's own
/// (``ChatMoreSpeakerMark/description``), because "maybe Ana" read out with
/// no verb is not a sentence.
struct ChatMoreSpeakerMarkView: View {

    let mark: ChatMoreSpeakerMark

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "waveform")
                .font(.caption2)
                .foregroundStyle(Color.fgMuted)
            Text(mark.label)
                .font(.caption2)
                .foregroundStyle(mark.soft ? Color.fgMuted : Color.brandInk)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(mark.description)
    }
}

/// The app's own version, for the report's `App:` line.
///
/// `CFBundleShortVersionString` is `MARKETING_VERSION` (`project.yml:44`) and
/// the build is `CURRENT_PROJECT_VERSION`; Android's report carries
/// `versionName (versionCode)` (`ReplyReport.kt:171`), so both go in.
enum ChatMoreAppVersion {

    static let short: String = {
        let info = Bundle.main.infoDictionary
        let name = info?["CFBundleShortVersionString"] as? String ?? ""
        let build = info?["CFBundleVersion"] as? String ?? ""
        if name.isEmpty { return build }
        return build.isEmpty ? name : "\(name) (\(build))"
    }()
}
