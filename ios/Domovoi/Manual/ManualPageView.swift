import DomovoiKit
import SwiftUI

// ---------------------------------------------------------------------------
// The manual's pages: Start here, a piece, and every kind of block the
// document can hold.
//
// `ManualScreen.kt:710` (`StartPage`), `:737` (`PiecePage`), `:782` (`Block`),
// `:833` (`SayRow`), `:861` (`ManualText`) and `:1009` (`Checklist`).
//
// **Nothing in this file holds a sentence.** Every word is the document's own
// (`doc.label(…)`, a block's `text`) or ``ManualStrings``, which is ported
// word for word from the Kotlin — the manual is the server's document and a
// literal here would be a second source of truth that drifts from it.
// ---------------------------------------------------------------------------

/// A page's content, in its own scroll view or bare.
///
/// The manual's three levels each scroll under a pinned top bar at the
/// ordinary type sizes and sit inside the screen's one scroll view at an
/// accessibility size, where the bar alone is most of the screen
/// (``ManualView/ready(_:cached:_:)``). This is the whole of that difference,
/// in one place, so a level never has to know which it is.
struct ManualScrollIf<Content: View>: View {

    private let scrolls: Bool
    private let content: Content

    /// Written out rather than left to the memberwise initialiser: a
    /// `@ViewBuilder` on a stored property works, and a builder spelt out in
    /// the initialiser is the shape every other container in this app has.
    init(scrolls: Bool, @ViewBuilder content: () -> Content) {
        self.scrolls = scrolls
        self.content = content()
    }

    var body: some View {
        if scrolls {
            ScrollView { content }
        } else {
            content
        }
    }
}

/// Start here (`StartPage`, `ManualScreen.kt:710`): the eyebrow, the title,
/// every block the app shows, then the way to the map.
struct ManualStartPage: View {

    let doc: ManualDoc
    let manual: ManualModel
    /// ``ManualView/ready(_:cached:_:)``'s decision: false when the screen
    /// has put the top bar and this page in one scroll view of its own.
    var scrolls: Bool = true

    var body: some View {
        ManualScrollIf(scrolls: scrolls) {
            LazyVStack(alignment: .leading, spacing: 8) {
                Text(doc.label("eyebrow", fallback: ManualStrings.Fallback.eyebrow))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Text(doc.start.title)
                    .font(.title2)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)
                // `shownInApp` drops the dashboard-only blocks — one in the
                // shipped document (`ManualContent.kt:180`).
                ForEach(doc.start.blocks.indices, id: \.self) { at in
                    let block = doc.start.blocks[at]
                    if shownInApp(block.only) {
                        if block.type == "checklist" {
                            ManualChecklistView(doc: doc, manual: manual)
                        } else {
                            ManualBlockView(block: block, doc: doc, manual: manual)
                        }
                    }
                }
                Button {
                    manual.go("map")
                } label: {
                    Label(
                        doc.label("show_map", fallback: ManualStrings.Fallback.showMap),
                        systemImage: "map"
                    )
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(minHeight: 44)
                }
                .buttonStyle(.borderedProminent)
                .padding(.top, 8)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// One piece's page (`PiecePage`, `ManualScreen.kt:737`).
struct ManualPiecePage: View {

    let doc: ManualDoc
    let piece: ManualPiece
    let manual: ManualModel
    /// See ``ManualStartPage/scrolls``.
    var scrolls: Bool = true

    @Environment(\.dynamicTypeSize) private var typeSize

    /// ``ManualView``'s override, which hands a `doc:` link back to it and
    /// passes a real URL — this footer's `More detail` — to the system.
    @Environment(\.openURL) private var openURL

    var body: some View {
        ManualScrollIf(scrolls: scrolls) {
            LazyVStack(alignment: .leading, spacing: 8) {
                heading
                ForEach(piece.blocks.indices, id: \.self) { at in
                    let block = piece.blocks[at]
                    if shownInApp(block.only) {
                        ManualBlockView(block: block, doc: doc, manual: manual)
                    }
                }
                footer
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder private var heading: some View {
        // The topic's name above the piece's, lower-cased and tappable — the
        // second way up, beside the crumbs.
        //
        // **The 44 points are centred on the word, not hung under it.** This
        // frame was `alignment: .bottomLeading`, which pinned the 12-point ink
        // to the bottom edge of a 44-point box and left the other 32 points
        // empty above it. Two things were wrong with that, and they are the
        // same fault seen from either end:
        //
        //  - A tap 25 points above the word, in the white between the search
        //    field and the page, navigated to the topic. A 44-point target is
        //    there so the control is big enough to hit, not so it reaches up
        //    into whitespace that draws nothing (Kamron's no-misleading-
        //    controls rule). Every other 44-point text target in this app
        //    centres its text — ``ManualCrumb``, `HomeRoomsView:285`,
        //    `HomeTodayView:126`, `NewsSavedFeedView:139` — and
        //    `.bottomLeading` was the only one of its kind in `ios/Domovoi/`.
        //  - Apple's audit named this element, and only this one, with
        //    `Contrast failed` on train R001. It is **not** a colour: sampled
        //    off that run's own screenshot, the ink here is exactly
        //    `(151, 88, 0)` on white — **5.66:1** — which is the same pixel
        //    value, to the unit, as the three ``ManualCrumb`` buttons and
        //    `zoom out` eleven points above it, all of them `brandInk` and all
        //    of them unnamed in the same audit of the same page. What was
        //    unique to this element was its shape: a row profile of the
        //    screenshot puts its ink in points 353.3–362.3 of a frame running
        //    321.3–365.3, three points off the bottom edge with 32 points of
        //    blank above, where every crumb's ink sits near its frame's
        //    middle. The rig has already recorded one `Contrast failed`
        //    against an element the videos grid's own bottom edge was cutting
        //    in half (`StubHouseUITests.frame(in:)`), so an element whose own
        //    frame all but clips its ink is the structure to correct — and no
        //    colour is repainted to make a finding go away.
        //
        // The cost is that the eyebrow sits about eleven points further from
        // its title (22 points rather than 11) and eighteen points nearer the
        // search field (30 rather than 48), so it still groups with the title
        // it belongs to. Android gives it `padding(vertical = 4.dp)` and no
        // enlarged target at all (`ManualScreen.kt:748`); 44 points is this
        // port's rule, and this is 44 points spent symmetrically.
        Button {
            manual.go(piece.topic)
        } label: {
            Text((doc.topic(piece.topic)?.title ?? "").lowercased())
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
                .frame(minHeight: 44, alignment: .leading)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        Text(piece.title)
            .font(.title2)
            .accessibilityAddTraits(.isHeader)
            .fixedSize(horizontal: false, vertical: true)
        // The same two words the map folds into a node's subtitle
        // (``manualPieceTags(_:_:)``), as pills here — which is what Android
        // does on both surfaces.
        let tags = manualPieceTags(piece, doc)
        if !tags.isEmpty {
            HStack(spacing: 6) {
                ForEach(tags, id: \.self) { tag in
                    ManualPill(label: tag, tone: .idle)
                }
            }
        }
    }

    /// `more` on the left, `next: <title>` on the right (`:764`). A row of two
    /// phrases becomes a column once the phrases are long.
    @ViewBuilder private var footer: some View {
        let next = manual.next(after: piece)
        if piece.more != nil || next != nil {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 4) { footerControls(next) }
                    .padding(.top, 8)
            } else {
                HStack(alignment: .firstTextBaseline) { footerControls(next) }
                    .padding(.top, 8)
            }
        }
    }

    @ViewBuilder private func footerControls(_ next: ManualPiece?) -> some View {
        if let more = piece.more {
            Button {
                // `openDoc` resolves the ref from the **server's** version, so
                // the link lands on the documentation of the house being read
                // about (`docRef`, `ManualContent.kt:288`).
                if case .web(let url) = manual.openDoc(more.target), let target = URL(string: url) {
                    openURL(target)
                }
            } label: {
                Label(more.text, systemImage: "arrow.up.right.square")
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
        }
        if !typeSize.isAccessibilitySize { Spacer(minLength: 8) }
        if let next {
            Button {
                manual.go(next.id)
            } label: {
                Text(
                    ManualStrings.next(
                        doc.label("next", fallback: ManualStrings.Fallback.next),
                        piece: next.title
                    )
                )
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .frame(minHeight: 44)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
        }
    }
}

/// The map, with the hint line under it (`ManualBody`'s third branch,
/// `ManualScreen.kt:349`).
struct ManualMapPage: View {

    let doc: ManualDoc
    let manual: ManualModel
    let focus: ManualTopic?
    let reduceMotion: Bool
    /// See ``ManualStartPage/scrolls``.
    var scrolls: Bool = true

    var body: some View {
        ManualScrollIf(scrolls: scrolls) {
            VStack(alignment: .leading, spacing: 0) {
                ManualMapStage(
                    doc: doc,
                    manual: manual,
                    focus: focus,
                    reduceMotion: reduceMotion
                )
                // The topic's own introduction at level two, the document's
                // map hint at level one, nothing when the document has
                // neither (`:353`).
                Text(focus?.intro ?? (focus == nil
                    ? doc.label("map_hint", fallback: ManualStrings.Fallback.mapHint)
                    : ""))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.vertical, 8)
            }
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

// MARK: - blocks

/// One block of a page (`Block`, `ManualScreen.kt:782`).
///
/// Seven types are drawn and **anything else draws nothing**, which is the
/// Kotlin's `else -> Unit` and the document's forward compatibility: a newer
/// server may add a block this build has never heard of, and a page that
/// renders its raw JSON would be worse than one that leaves it out.
struct ManualBlockView: View {

    let block: ManualBlock
    let doc: ManualDoc
    let manual: ManualModel

    var body: some View {
        switch block.type {
        case "p":
            ManualRichText(text: block.text, manual: manual)
        case "list":
            list
        case "label":
            label
        case "say":
            say
        case "table":
            table
        case "live":
            ManualLiveSlotView(slot: block.slot, doc: doc, manual: manual)
        default:
            EmptyView()
        }
    }

    /// `•` or `1.`, then the item (`:789`).
    private var list: some View {
        let items = block.items.filter { shownInApp($0.only) }
        return VStack(alignment: .leading, spacing: 4) {
            ForEach(items.indices, id: \.self) { at in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(block.ordered ? "\(at + 1)." : "\u{2022}")
                        .font(.body)
                        .foregroundStyle(Color.fgMuted)
                        .accessibilityHidden(true)
                    ManualRichText(text: items[at].text, manual: manual)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// A small heading inside a page (`:798`). `role == "wrong"` is the one
    /// that changes colour, and it takes ``Color/warnInk`` — never the amber
    /// fill, because this is text.
    private var label: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(block.text)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(block.role == "wrong" ? Color.warnInk : Color.primary)
                .fixedSize(horizontal: false, vertical: true)
            if block.admin {
                ManualPill(
                    label: doc.label("admin", "text", fallback: ManualStrings.Fallback.admin),
                    tone: .warn
                )
            }
            if let note = block.note {
                Text(note)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.top, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Things to say (`:805`).
    ///
    /// ``manualSayRows(_:features:)`` filters **then** pairs, in that order:
    /// a phrase dropped because the server cannot answer it takes its reply
    /// with it, and the phrase above it does not inherit that reply.
    private var say: some View {
        let rows = manualSayRows(block.phrases, features: manual.features)
        return VStack(alignment: .leading, spacing: 5) {
            ForEach(rows) { row in
                ManualSayRowView(row: row, doc: doc, manual: manual)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Two cells a row, stacked, in a bordered column (`:816`).
    ///
    /// **The header is ignored**, ported exactly: the Kotlin draws
    /// `b.rows` and never `b.header`, and the two tables in the shipped
    /// document read as pairs without one.
    private var table: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(block.rows.indices, id: \.self) { at in
                if at > 0 { Divider() }
                VStack(alignment: .leading, spacing: 2) {
                    if let first = block.rows[at].first {
                        ManualRichText(text: first, manual: manual)
                    }
                    if block.rows[at].count > 1 {
                        ManualRichText(
                            text: block.rows[at][1],
                            manual: manual,
                            scale: .small,
                            colour: Color.fgMuted
                        )
                    }
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(.quaternary)
        }
    }
}

/// A phrase and the reply it gets (`SayRow`, `ManualScreen.kt:833`).
struct ManualSayRowView: View {

    let row: ManualSayRow
    let doc: ManualDoc
    let manual: ManualModel

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // Android's `FlowRow` wraps; a column is what that becomes once a
        // phrase plus `then` plus a second phrase cannot share a line.
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) { parts }
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(alignment: .firstTextBaseline, spacing: 6) { parts }
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder private var parts: some View {
        ManualPhraseChip(text: row.phrase.text, wake: manual.wake.lead, doc: doc)
        if let then = row.then {
            Text(ManualStrings.then)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            ManualPhraseChip(text: then.text, wake: manual.wake.lead, doc: doc)
        }
        if let note = row.phrase.note {
            ManualRichText(text: note, manual: manual, scale: .small, colour: Color.fgMuted)
        }
        if row.phrase.pending != nil {
            ManualPill(
                label: doc.label("pending", "text", fallback: ManualStrings.Fallback.pending),
                tone: .idle
            )
        }
    }
}

/// Something to say, in curly quotes on a sunken chip (`Phrase`,
/// `ManualScreen.kt:846`).
///
/// `{wake}` is substituted here too: a phrase is written `“{wake}, what time
/// is it”` in the document and a chip showing the braces would be a chip
/// nobody can say out loud.
struct ManualPhraseChip: View {

    let text: String
    let wake: String
    let doc: ManualDoc

    var body: some View {
        Text("\u{201C}\(spoken)\u{201D}")
            .font(.system(.footnote, design: .monospaced))
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(
                Color(.secondarySystemBackground),
                in: RoundedRectangle(cornerRadius: 6, style: .continuous)
            )
            .overlay {
                RoundedRectangle(cornerRadius: 6, style: .continuous).strokeBorder(.quaternary)
            }
    }

    /// The phrase as it is said. The chip carries no markup of its own — a
    /// phrase is one sentence — so this is the one substitution it needs.
    private var spoken: String {
        guard text.contains("{wake}") else { return text }
        let word = manualFill(doc.wakeWord.quoted, ["phrase": wake])
        return text.replacingOccurrences(of: "{wake}", with: word)
    }
}

/// One word in a tone, on an opaque surface.
///
/// Opaque on purpose, which is ``HomeTonePill``'s lesson and not a style
/// preference: a contrast audit cannot read a colour that is whatever happens
/// to be behind it, and `Color(.secondarySystemBackground)` is a colour.
/// The ink is always a token — `warnInk`, `okInk`, `fgMuted` — never
/// `.orange`, `.green` or `.red`, which measure 2.2:1 and 2.2:1 and 3.55:1 as
/// text.
struct ManualPill: View {

    enum Tone { case warn, ok, idle }

    let label: String
    let tone: Tone

    var body: some View {
        Text(label)
            .font(.caption)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(ink)
            .background(Color(.secondarySystemBackground), in: Capsule())
    }

    private var ink: Color {
        switch tone {
        case .warn: return Color.warnInk
        case .ok: return Color.okInk
        case .idle: return Color.fgMuted
        }
    }
}

// MARK: - the inline markup

/// How big a run of the manual's text is drawn.
///
/// The scale travels with the text rather than being a `.font` modifier on
/// top of it, because the spans set fonts of their own — a bold run, a code
/// run — and a span's font wins over the modifier. A `small` run whose code
/// spans were `.body` would be a note with one word in it twice the size.
enum ManualTextScale: Equatable {
    case body
    case small

    var style: Font.TextStyle { self == .body ? .body : .footnote }
    var font: Font { .system(style) }
    var mono: Font { .system(style, design: .monospaced) }
}

/// The manual's inline markup, drawn (`ManualText`, `ManualScreen.kt:861`).
///
/// ### A `route:` link the app cannot open is plain text
///
/// ``ManualModel/linkIsLive(kind:target:)`` decides, and it is the whole
/// reason this is not a one-line `Text(.init(markdown:))`: a link to Chat on
/// a server whose AI features are off, or to a screen this build does not
/// have, must read as the words it is and not as a link that bounces back to
/// Home. The Kotlin has a comment at `:895` saying so, which is where the
/// rule was found.
struct ManualRichText: View {

    let text: String
    let manual: ManualModel
    var scale: ManualTextScale = .body
    var colour: Color = .primary

    var body: some View {
        Text(attributed)
            .font(scale.font)
            .foregroundStyle(colour)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var attributed: AttributedString {
        var out = AttributedString()
        for span in parseManualInline(text) {
            out += run(of: span)
        }
        return out
    }

    private func run(of span: ManualSpan) -> AttributedString {
        switch span {
        case .plain(let text):
            return AttributedString(text)
        case .bold(let text):
            var run = AttributedString(text)
            run.font = scale.font.weight(.semibold)
            return run
        case .italic(let text):
            var run = AttributedString(text)
            run.font = scale.font.italic()
            return run
        case .code(let text):
            // Monospace **without** the sunken wash Android gives it: an
            // inline background inside a `Text` is a colour Apple's contrast
            // audit cannot attribute to the run that owns it, and the
            // typeface already tells code from prose. Recorded in DECISIONS.
            var run = AttributedString(text)
            run.font = scale.mono
            return run
        case .wake:
            var run = AttributedString(
                manualFill(manual.doc?.wakeWord.quoted ?? "", ["phrase": manual.wake.lead])
            )
            run.font = scale.font.weight(.semibold)
            return run
        case .tag(let name):
            var run = AttributedString(" \(word(of: name)) ")
            run.font = .system(.caption2)
            run.foregroundColor = name == "admin" ? Color.warnInk : Color.fgMuted
            return run
        case .link(let text, let kind, let target):
            guard manual.linkIsLive(kind: kind, target: target),
                  let url = manualLinkURL(kind: kind, target: target)
            else {
                // Plain text, and nothing marking where the link would have
                // been: a dead underline is the misleading control this rule
                // exists to prevent.
                return AttributedString(text)
            }
            var run = AttributedString(text)
            run.link = url
            run.foregroundColor = Color.brandInk
            run.underlineStyle = .single
            return run
        }
    }

    /// A tag's word comes from the document (`labels.admin.text`), with the
    /// Kotlin's own fallback when the document has none.
    private func word(of name: String) -> String {
        guard let doc = manual.doc else { return name }
        switch name {
        case "admin":
            return doc.label("admin", "text", fallback: ManualStrings.Fallback.admin)
        case "preview":
            return doc.label("preview", "text", fallback: ManualStrings.Fallback.preview)
        default:
            return doc.label("plugin", "text", fallback: ManualStrings.Fallback.plugin)
        }
    }
}

// MARK: - the checklist

/// The setting-up checklist (`Checklist`, `ManualScreen.kt:1009`).
///
/// Three states per line, and the third is the one that matters:
/// ``ManualChecklistLine/done`` is nil when **this app cannot tell from
/// here** — a read that has not landed, or a signal only the dashboard has —
/// and that is drawn as neither ticked nor crossed, with the document's own
/// `checklist_unknown` as its label.
struct ManualChecklistView: View {

    let doc: ManualDoc
    let manual: ManualModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(manual.checklist) { line in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Image(systemName: line.done == true ? "checkmark.circle" : "circle")
                        .foregroundStyle(line.done == true ? Color.okInk : Color.fgMuted)
                        .accessibilityLabel(label(of: line.done))
                    ManualRichText(
                        text: line.text,
                        manual: manual,
                        colour: line.done == true ? Color.fgMuted : Color.primary
                    )
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func label(of done: Bool?) -> String {
        switch done {
        case true: return ManualStrings.checkDone
        case false: return ManualStrings.checkNotYet
        // `doc.label("checklist_unknown")` is read with **no** fallback on
        // Android (`:1017`), so a document without the key leaves this empty.
        // Ported as it stands and raised in DECISIONS rather than quietly
        // improved.
        default: return doc.label("checklist_unknown", fallback: ManualStrings.Fallback.checklistUnknown)
        }
    }
}
