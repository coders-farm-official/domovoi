import DomovoiKit
import SwiftUI

/// One chat kept on this phone: what it is, when it was last used, and the
/// four things this milestone can do to it.
///
/// `ChatRowCard` (`ChatHome.kt:577`) as `PhoneChatPlace` fills it, which is
/// ``ChatRowView`` with three differences and no others:
///
///  - **The chip** (`ChatHome.kt:691`). With two places a row has to say which
///    one it is from — the fact ``ChatRow`` once left out because "it would be
///    the same value on every row for ever".
///  - **No pin mark and no pin in the menu.** ``assistantPinKey(place:id:server:)``
///    says why at length: the shared pin book keys on the house's address, and
///    a phone chat filed under it would come back under a different house.
///  - **No globe.** `used_internet` is a lookup the house made (M5 here); a
///    phone chat in this milestone cannot go online at all, so the mark could
///    only ever be absent and is not drawn.
///
/// The menu is **exactly** ``AssistantPhoneModel/actions(for:)`` — the order is
/// Android's `rowActions` (`ChatPlace.kt:121`) and is unit-tested at both ends,
/// so this view does not build one of its own.
struct AssistantPhoneRowView: View {

    let row: ChatRow
    let phone: AssistantPhoneModel
    /// Relative age, from the model's clock. The model owns `nowMs`, so a
    /// test can move it; a view reading `Date()` could not be moved at all.
    let age: String

    /// A fixed `frame(width:)` on a scaling glyph draws the icon across the
    /// words beside it at AX5, and no automated check catches it
    /// (``ChatView`` paid for that one). Every glyph width here is scaled.
    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 26

    /// At an accessibility size this row's two side-by-side pairs become
    /// columns — the title beside its place chip, and the words beside the
    /// age. Train S005's picture is the argument: at the **ordinary** size the
    /// title was already down to 94 points of width with the chip and the age
    /// holding the rest, so a five-word title wrapped to three lines; at AX5
    /// the same `HStack` would have left it a column of single letters.
    /// NIGHT-RULES §3 asks for the swap on every row that has one.
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        HStack(spacing: 8) {
            Button {
                phone.open(row)
            } label: {
                face
            }
            .buttonStyle(.plain)
            .accessibilityLabel(row.title)
            // The age, which is also what tells a row for an unnamed chat
            // apart from the header's `new chat` button: both are labelled
            // "new chat" and only a row carries a value.
            .accessibilityValue(age)
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
                // ``ChatRow/subtitle``: the search's match where the title was
                // not what matched, and **nothing at all** where it is blank.
                // A phone chat has no server snippet, so for a row that is not
                // a search hit this is nil and the row is two lines, not three
                // with an empty one.
                if let said = row.subtitle {
                    Text(said)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                // At an accessibility size the age joins the column instead of
                // competing with the title for the row's width.
                if typeSize.isAccessibilitySize { ageText }
            }
            Spacer(minLength: 0)
            if !typeSize.isAccessibilitySize { ageText }
        }
        .frame(minHeight: 56)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(.rect)
    }

    private var ageText: some View {
        Text(age)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .monospacedDigit()
            .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder
    private var title: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 2) { titleWords }
        } else {
            HStack(spacing: 6) { titleWords }
        }
    }

    @ViewBuilder
    private var titleWords: some View {
        Group {
            Text(row.title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Text(AssistantStrings.chipPhone)
                .font(.caption2)
                .foregroundStyle(Color.brandInk)
                .fixedSize(horizontal: false, vertical: true)
            // The archived mark, for ``ChatRowView``'s reason: in the archived
            // view it is every row, and in the main list a search hit can be
            // an archived chat — ``assistantSearchConversations(_:_:)``
            // searches both halves, as the server's `?q=` does — so the mark
            // is the only thing that says where the chat lives.
            if row.archived {
                Text(ChatStrings.archived)
                    .font(.caption2)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// The menu the model gives, in its order, with nothing added.
    ///
    /// A `switch` over the words rather than a list of `(label, action)` pairs
    /// built here, which is ``ChatRowView``'s own argument: the membership and
    /// the order are the model's answer, and a second place that decides them
    /// is how `pin` comes to be offered on a row that cannot hold one.
    private var menu: some View {
        Menu {
            ForEach(phone.actions(for: row), id: \.self) { action in
                Button(action) { perform(action) }
            }
        } label: {
            Image(systemName: "ellipsis.circle")
                .foregroundStyle(Color.fgMuted)
                .frame(width: 44, height: 44)
                .contentShape(.rect)
        }
        .accessibilityLabel(ChatStrings.rowActions)
        .accessibilityValue(row.title)
    }

    private func perform(_ action: String) {
        switch action {
        case ChatStrings.actionRename: phone.renameRequested(row)
        case ChatStrings.actionArchive: phone.setArchived(row, true)
        case ChatStrings.actionUnarchive: phone.setArchived(row, false)
        case ChatStrings.actionShare: phone.share(row)
        case ChatStrings.actionDelete: phone.deleteRequested(row)
        // Unreachable while this covers ``AssistantPhoneModel/actions(for:)``,
        // and silent on purpose: a menu item the model grew and this did not
        // is a missing branch to find in review, not something to guess at on
        // somebody's phone. `pin` and `unpin` are the two the model filters
        // out, and they would land here if it ever stopped.
        default: break
        }
    }
}
