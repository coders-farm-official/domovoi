import DomovoiKit
import SwiftUI

/// Home's **announce** card — a port of `HomeAnnounce`
/// (`HomeSections.kt:847`), map §3.5.
///
/// ### The draft and the send are NOT in this view, deliberately
///
/// They are ``HomeView``'s, and that is the whole point of this file's shape.
/// Android hoists them out of the section into `HomeScreen` and says why at
/// `HomeScreen.kt:347`: "the section is a `LazyColumn` item, and scrolling it
/// out of view while a send is in flight would cancel the request (and its
/// toast) with it". **A `LazyVStack` row holding a `Task` is the identical bug
/// on iOS** — a `.task` or a `Task` started inside a lazy row is cancelled
/// when the row is recycled, so a person who scrolled down to look at the
/// rooms after tapping send would get no announcement and no toast telling
/// them so.
///
/// Home's column is a plain `VStack` today, which does not have the problem —
/// but it is one `LazyVStack` away from having it, and §7.6 forbids
/// pagination here, so there is no reason the column would ever become lazy.
/// The hoist costs two bindings and removes the hazard whichever way that
/// goes. The same reasoning put the timer cancel and the transport writes in
/// the screen.
///
/// So this view is given a `Binding` to the draft and a closure to call, and
/// holds nothing it could lose.
///
/// Every string is ``HomeAnnounceStrings``'s and both enablement rules are the
/// package's: ``announceOnlineCount(onlineRooms:coreDown:)`` — which forces
/// the count to zero while the core is down, "so the field greys out rather
/// than offering to speak into a house that cannot hear" — and
/// ``announceSendEnabled(message:onlineCount:sending:)``.
struct HomeAnnounceView: View {

    /// ``announceOnlineCount(onlineRooms:coreDown:)``'s answer, not the raw
    /// room count: zero while the core is down.
    let onlineCount: Int

    @Binding var message: String

    let sending: Bool
    let onSend: () -> Void

    @Environment(\.horizontalSizeClass) private var sizeClass

    @Environment(\.dynamicTypeSize) private var typeSize

    private var compact: Bool { ShellWidth(sizeClass) == .compact }

    private var enabled: Bool {
        announceSendEnabled(message: message, onlineCount: onlineCount, sending: sending)
    }

    var body: some View {
        HomeCard(title: HomeAnnounceStrings.title) {
            pill
            if typeSize.isAccessibilitySize {
                // The button goes under the field rather than beside it: a
                // field wide enough to type into and a 44-point button do not
                // share a line at AX5.
                VStack(alignment: .leading, spacing: 8) {
                    field
                    send
                }
            } else {
                HStack(alignment: .center, spacing: 8) {
                    field
                    send
                }
            }
        }
    }

    /// The header pill: `no rooms online` (idle) or `<n> online` (brand).
    ///
    /// It is in the card's body rather than in ``HomeCard``'s header slot
    /// because that slot is a link, and this is a status. Android puts it in
    /// the header; the difference is cosmetic and is not worth a second shape
    /// in the shared card.
    private var pill: some View {
        HStack {
            HomeTonePill(
                label: onlineCount == 0
                    ? HomeAnnounceStrings.noRoomsOnline
                    : HomeAnnounceStrings.onlinePill(onlineCount),
                tone: onlineCount == 0 ? .idle : .brand
            )
            Spacer(minLength: 0)
        }
    }

    /// The field: one line's worth, wrapping rather than scrolling sideways,
    /// **disabled when no room is online**.
    ///
    /// `axis: .vertical` and `lineLimit(1...)` for the reason the server
    /// address field in Settings got them: Apple's audit failed a single-line
    /// field for "Text of this element may be clipped at larger Dynamic Type
    /// sizes", and a message you can only see half of is worse than a short
    /// one. The border is drawn here rather than by
    /// `.textFieldStyle(.roundedBorder)`, which with a vertical axis produces
    /// an 18-point control the audit then fails for its hit area.
    private var field: some View {
        TextField(
            HomeAnnounceStrings.placeholder,
            text: $message,
            axis: .vertical
        )
        .lineLimit(1...)
        .font(.subheadline)
        .padding(.horizontal, 8)
        .padding(.vertical, 10)
        .frame(minHeight: 44)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
        .overlay {
            RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary)
        }
        .disabled(onlineCount == 0)
        // The placeholder vanishes the moment anything is typed, so it cannot
        // be the label.
        .accessibilityLabel(HomeAnnounceStrings.announceInEveryRoom)
        // `imeOptions = Send`. The submit is the same closure as the button,
        // so the no-op branches toast there too
        // (``announceAttempt(message:onlineCount:sending:)``) rather than
        // doing nothing.
        .onSubmit(onSend)
        .submitLabel(.send)
    }

    /// A megaphone alone on a phone, the megaphone plus `send` on a wide pane.
    ///
    /// On a phone the glyph is all there is, so
    /// ``HomeAnnounceStrings/announceInEveryRoom`` is the only thing that says
    /// what the button does — and it is 52 × 44 on Android, which is already
    /// over Apple's minimum in both directions.
    private var send: some View {
        Button(action: onSend) {
            HStack(spacing: 6) {
                Image(systemName: "megaphone.fill")
                if !compact {
                    Text(HomeAnnounceStrings.send)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .font(.subheadline)
            .frame(minWidth: 52, minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.borderedProminent)
        // The app's tint is `brandInk`, so a prominent button would fill with
        // the ink and draw near-black `brandFg` on it at 3.08:1. The fill is
        // the other token, and 8.49:1 with this label colour.
        .tint(Color.brand)
        .foregroundStyle(Color.brandFg)
        .disabled(!enabled)
        .accessibilityLabel(HomeAnnounceStrings.announceInEveryRoom)
    }
}
