import DomovoiKit
import SwiftUI

// ---------------------------------------------------------------------------
// The four live slots: the places where the manual stops describing the house
// and shows it.
//
// `LiveSlot` (`ManualScreen.kt:918`) and `ThingsRow` (`:982`). A `live` block
// carries a `slot` name and the document decides where one goes; the shipped
// file has five of them — `wake-word` twice, `things-to-say`, `rooms` and
// `plugins` — and **a name this build does not know draws nothing**, which is
// the Kotlin's `else -> Unit` (`:977`) and the document's forward
// compatibility.
//
// **Every slot draws nothing at all while its read has not answered.** That
// is `live.rooms?.let` on Android (`:936`) and an optional here, and it is
// the manual's own promise in `notes.live`: "Nothing extra shows when it
// can't be read." Nil is not an empty list — an empty list is a house with no
// rooms, and that has wording of its own.
// ---------------------------------------------------------------------------

/// The route a room row opens. The document's own id, which is **not** the
/// one Android asks for.
///
/// `ManualScreen.kt:951` taps through to `settings/satellites`, and the
/// shipped document's route table has no such key — it has `settings/rooms`.
/// `onRoute`'s `r == null -> Unit` (`:324`) swallows the miss, so every room
/// row in the shipping app announces itself as a button and does nothing.
/// Reported as an Android defect, not copied.
let manualRoomsRoute = "settings/rooms"

/// One `live` block (`LiveSlot`, `ManualScreen.kt:918`).
struct ManualLiveSlotView: View {

    let slot: String?
    let doc: ManualDoc
    let manual: ManualModel

    var body: some View {
        switch slot {
        case "wake-word":
            ManualWakeNoteBox(manual: manual)
        case "rooms":
            ManualRoomsBox(doc: doc, manual: manual)
        case "plugins":
            ManualPluginsBox(doc: doc, manual: manual)
        case "things-to-say":
            ManualThingsToSayBox(doc: doc, manual: manual)
        default:
            EmptyView()
        }
    }
}

/// "Some rooms answer to another word: …" (`:919`), and **nothing at all when
/// every room answers to the one the manual names**.
///
/// ``manualWakeNote(_:_:)`` returns nil then, which is the whole point of the
/// slot: a house that never changed its wake word is never told about one.
struct ManualWakeNoteBox: View {

    let manual: ManualModel

    var body: some View {
        if let note = manual.wakeNote {
            Text(note)
                .font(.footnote)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .background(
                    Color.brand.opacity(0.12),
                    in: RoundedRectangle(cornerRadius: 10, style: .continuous)
                )
        }
    }
}

/// The rooms, as they are right now (`:935`).
struct ManualRoomsBox: View {

    let doc: ManualDoc
    let manual: ManualModel

    /// ``ManualView``'s override: a `route:` link minted here comes back to
    /// it and is resolved through the document's own route table, exactly as
    /// a link in a paragraph is.
    @Environment(\.openURL) private var openURL

    var body: some View {
        if let rooms = manual.live.rooms {
            ManualLiveBox {
                if rooms.isEmpty {
                    ManualLiveEmpty(
                        text: doc.label("rooms_none", fallback: ManualStrings.Fallback.roomsNone)
                    )
                }
                ForEach(rooms.indices, id: \.self) { at in
                    if at > 0 { Divider() }
                    row(rooms[at])
                }
            }
        }
    }

    /// A room's row. **A button only when there is somewhere for it to go**:
    /// ``ManualModel/openRoute(_:)`` answers `.nothing` for a route the
    /// document does not define, and a row that announces itself as a button
    /// and does nothing is the control the owner's rule forbids
    /// ([[no-misleading-controls]]) — and is what Android ships.
    @ViewBuilder private func row(_ room: ManualRoom) -> some View {
        if manual.openRoute(manualRoomsRoute) != .nothing,
           let url = manualLinkURL(kind: "route", target: manualRoomsRoute) {
            Button {
                openURL(url)
            } label: {
                line(room)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(ManualStrings.roomLine([room.name, manualRoomLine(room, doc.wakeWord)]))
        } else {
            line(room)
        }
    }

    @ViewBuilder private func line(_ room: ManualRoom) -> some View {
        let detail = manualRoomLine(room, doc.wakeWord)
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            ManualStatusDot(status: room.status)
            // Already a column: the name and its line never shared one, so
            // there is nothing here to swap at an accessibility size — only
            // the dot sits beside them, and a dot is 8 points wide at every
            // type size by design.
            VStack(alignment: .leading, spacing: 2) {
                name(room)
                sub(detail)
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
    }

    private func name(_ room: ManualRoom) -> some View {
        Text(room.name)
            .font(.subheadline.weight(.semibold))
            .fixedSize(horizontal: false, vertical: true)
    }

    /// `"<status> · <“wake”> · <what it is playing>"` (`:957`).
    ///
    /// The third part is the one **Android cannot show**: it decodes
    /// `now_playing.title` while the server carries the track under
    /// `now_playing.song` (`ManualRoom/song`).
    private func sub(_ detail: String) -> some View {
        Text(detail)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// A room's state, as a dot.
///
/// **Never `.green`/`.red`/`.orange`**: those are 2.22:1, 3.55:1 and 2.20:1
/// on the light canvas and a dot that carries meaning needs 3:1. The ink
/// tokens are what ``ToneDot`` settled on for the same reason. Hidden from
/// VoiceOver because the word is right beside it in the line.
struct ManualStatusDot: View {

    let status: String

    var body: some View {
        Circle()
            .fill(ink)
            .frame(width: 8, height: 8)
            .offset(y: -2)
            .accessibilityHidden(true)
    }

    private var ink: Color {
        switch status {
        case "online": return Color.okInk
        case "waiting": return Color.warnInk
        default: return Color.fgMuted
        }
    }
}

/// The plugins this house has (`:952`).
struct ManualPluginsBox: View {

    let doc: ManualDoc
    let manual: ManualModel

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        if let plugins = manual.live.plugins {
            ManualLiveBox {
                if plugins.isEmpty {
                    ManualLiveEmpty(
                        text: doc.label(
                            "plugins_none",
                            fallback: ManualStrings.Fallback.pluginsNone
                        )
                    )
                }
                ForEach(plugins.indices, id: \.self) { at in
                    if at > 0 { Divider() }
                    row(plugins[at])
                }
            }
        }
    }

    @ViewBuilder private func row(_ plugin: ManualPluginRow) -> some View {
        // A name, a version and a pill cannot share a line once the type is
        // big; the row becomes a column (standing rule).
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) { parts(plugin) }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(alignment: .firstTextBaseline, spacing: 8) { parts(plugin) }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder private func parts(_ plugin: ManualPluginRow) -> some View {
        Text(plugin.name ?? plugin.slug)
            .font(.subheadline)
            .fixedSize(horizontal: false, vertical: true)
        if !typeSize.isAccessibilitySize { Spacer(minLength: 4) }
        if let version = plugin.version {
            Text(version)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        // `disabled` when it is installed and switched off, else the server's
        // own word for its state (`:962`).
        ManualPill(
            label: plugin.enabled ? (plugin.status ?? "") : ManualStrings.pluginDisabled,
            tone: plugin.enabled && plugin.status == "ok" ? .ok : .idle
        )
    }
}

/// Everything the house can do, with something to say to each of them
/// (`:966`, `ThingsRow` at `:982`).
struct ManualThingsToSayBox: View {

    let doc: ManualDoc
    let manual: ManualModel

    var body: some View {
        if manual.live.handlers != nil {
            // ``ManualModel/listedHandlers`` applies
            // ``manualListedHandlers(_:_:features:)``: a handler named in
            // `handler_needs_feature` is listed only while the server has
            // that feature **on**. Note the asymmetry with a `say` block,
            // which drops a phrase only when a feature is explicitly off —
            // a phrase is a suggestion, a handler in this list is a claim.
            let handlers = manual.listedHandlers
            ManualLiveBox {
                ForEach(handlers.indices, id: \.self) { at in
                    if at > 0 { Divider() }
                    ManualThingsRow(
                        handler: handlers[at],
                        said: handlers[at].said(
                            fallback: manual.phrasesByHandler[handlers[at].name] ?? []
                        ),
                        doc: doc,
                        wake: manual.wake.lead
                    )
                }
            }
        }
    }
}

/// One handler: its name, up to three things to say to it, and what it needs
/// (`ThingsRow`, `ManualScreen.kt:982`).
struct ManualThingsRow: View {

    let handler: ManualHandler
    let said: [String]
    let doc: ManualDoc
    let wake: String

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(handler.title)
                .font(.subheadline.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)
            // Android's `FlowRow`; a column at an accessibility size, where a
            // monospaced sentence in quotes already fills the width.
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 4) { phrases }
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 5) { phrases }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private var phrases: some View {
        if said.isEmpty {
            Text(doc.label("say_none", fallback: ManualStrings.Fallback.sayNone))
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        // `said` is already the server's own examples, else the manual's own
        // phrases for this handler, capped at three
        // (``ManualHandler/said(fallback:)``).
        ForEach(said.indices, id: \.self) { at in
            ManualPhraseChip(text: said[at], wake: wake, doc: doc)
        }
        if let origin = handler.origin, origin != "core" {
            ManualPill(
                label: "\(doc.label("plugin", "text", fallback: ManualStrings.Fallback.plugin)): \(origin)",
                tone: .idle
            )
        }
        // `labels.net.<value>` — a nested label, and **nothing drawn when the
        // document has no word for this value** (`:1000`), which is the
        // Kotlin's `if (words != null)`.
        if let net = handler.requiresNetwork {
            let words = doc.label("net", net)
            if !words.isEmpty {
                ManualPill(label: words, tone: net == "yes" ? .warn : .idle)
            }
        }
    }
}

/// A bordered column, which is what a slot's rows sit in (`LiveBox`,
/// `ManualScreen.kt:1001`).
struct ManualLiveBox<Content: View>: View {

    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay {
            RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(.quaternary)
        }
    }
}

/// A slot with nothing in it — a house with no rooms, no plugins (`LiveEmpty`,
/// `:1006`). **Not** the same as a read that has not answered, which draws no
/// box at all.
struct ManualLiveEmpty: View {

    let text: String

    var body: some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
