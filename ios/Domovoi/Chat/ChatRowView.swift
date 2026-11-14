import DomovoiKit
import SwiftUI

/// One chat in the history list: what it is, when it was last used, and the
/// five things this app can do to it.
///
/// `ChatRowCard` (`ChatHome.kt:577`). The menu is **exactly**
/// ``ChatModel/actions(for:)`` — the same five in the same order
/// (``ChatRules/rowActions(_:)``), with nothing added and nothing dropped,
/// because that order is Android's and is unit-tested at both ends.
struct ChatRowView: View {

    let row: ChatRow
    let chat: ChatModel

    /// Every glyph width in this folder, for ``ChatView``'s reason: a fixed
    /// `frame(width:)` on a scaling glyph draws the icon across the text
    /// beside it at AX5, and no automated check catches it.
    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 26
    @ScaledMetric(relativeTo: .body) private var markWidth: CGFloat = 18

    var body: some View {
        HStack(spacing: 8) {
            Button {
                chat.open(row)
            } label: {
                face
            }
            .buttonStyle(.plain)
            .accessibilityLabel(row.title)
            // The age, which is also what tells a row for an untitled thread
            // apart from the header's `new chat` button: both are labelled
            // "new chat" and only a row has a value.
            .accessibilityValue(chat.timeText(row))
            .accessibilityHint(ChatStrings.openRow)
            menu
        }
    }

    private var face: some View {
        HStack(spacing: 12) {
            Image(systemName: "bubble.left")
                .font(.body)
                .foregroundStyle(Color.fgMuted)
                .frame(width: glyphWidth, alignment: .center)
            VStack(alignment: .leading, spacing: 2) {
                title
                // `(row.match ?: row.snippet)?.takeIf { it.isNotBlank() }`
                // (`ChatHome.kt:626`), which is ``ChatRow/subtitle``: a
                // search's own match where the title was not what matched,
                // the thread's last words otherwise, and **nothing at all**
                // where either is blank — a reply that failed leaves the
                // server's snippet empty, and an empty line is worse than no
                // line.
                if let said = row.subtitle {
                    Text(said)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
            }
            Spacer(minLength: 0)
            Text(chat.timeText(row))
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .monospacedDigit()
        }
        .frame(minHeight: 56)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(.rect)
    }

    /// The title, with the two marks that go beside it.
    ///
    /// The pin is this phone's (``ChatRules/pinKey(id:server:)``) and the
    /// globe says a lookup in this chat went online (`used_internet`, V037).
    /// The `archived` mark is drawn wherever the row says it is archived,
    /// which in the archived view is every row and in the **main list** is a
    /// search hit: `?q=` with no `archived` searches both halves
    /// (`chat.py:235`), so an archived thread can turn up among the hits and
    /// the mark is the only thing that says where it lives.
    private var title: some View {
        HStack(spacing: 6) {
            Text(row.title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            if row.pinned {
                mark("pin.fill", ChatStrings.pinnedMark)
            }
            if row.usedInternet {
                mark("globe", ChatStrings.usedInternetMark)
            }
            if row.archived {
                Text(ChatStrings.archived)
                    .font(.caption2)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func mark(_ glyph: String, _ said: String) -> some View {
        Image(systemName: glyph)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .frame(width: markWidth, alignment: .center)
            .accessibilityLabel(said)
    }

    /// `rowActions`, in order, with nothing else in it.
    ///
    /// The labels come from the model and the dispatch below turns each back
    /// into the call it names. A `switch` over the strings rather than a list
    /// of `(label, action)` pairs built here, because the **membership and the
    /// order** are the model's answer and a pair list would be a second place
    /// that decides them — which is how the pin item comes to be offered on a
    /// row that is already pinned.
    private var menu: some View {
        Menu {
            ForEach(chat.actions(for: row), id: \.self) { action in
                Button(action) { perform(action) }
            }
        } label: {
            Image(systemName: "ellipsis.circle")
                .foregroundStyle(Color.fgMuted)
                .frame(width: 44, height: 44)
                .contentShape(.rect)
        }
        // `chat actions` is Android's own word for this control
        // (`ChatHome.kt:582`) and it is the same on every row, so the row it
        // belongs to is its value. There is no `moreFor`-style string in
        // ``ChatStrings`` to spell it into one label, and inventing one would
        // have been a sentence this port says and Android does not.
        .accessibilityLabel(ChatStrings.rowActions)
        .accessibilityValue(row.title)
    }

    private func perform(_ action: String) {
        switch action {
        case ChatStrings.actionRename: chat.renameRequested(row)
        case ChatStrings.actionPin: chat.setPinned(row, true)
        case ChatStrings.actionUnpin: chat.setPinned(row, false)
        case ChatStrings.actionArchive: chat.setArchived(row, true)
        case ChatStrings.actionUnarchive: chat.setArchived(row, false)
        case ChatStrings.actionShare: chat.share(row)
        case ChatStrings.actionDelete: chat.deleteRequested(row)
        // Unreachable while this `switch` covers ``ChatRules/rowActions(_:)``,
        // and silent on purpose: a menu item the model grew and this did not
        // is a missing branch to find in review, not something to guess at on
        // somebody's phone.
        default: break
        }
    }
}
