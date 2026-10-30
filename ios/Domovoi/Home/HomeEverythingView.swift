import DomovoiKit
import SwiftUI

/// Home's **everything** grid — a port of `HomeEverything`
/// (`HomeSections.kt:1021`), map §3.7.
///
/// **Phones only** (`HomeScreen.kt:497`), "because the rail and the drawer
/// already list every screen". ``HomeView`` makes that decision; this view
/// draws what it is given.
///
/// The tiles are ``everythingTiles(_:bar:)``'s — every launcher screen **not**
/// on this phone's bar that this server offers and that a shared screen may
/// show — which ``ShellModel/everything`` already computes for the sidebar.
/// The grid returning nothing is a real state: with every screen on the bar
/// the card is not drawn at all.
struct HomeEverythingView: View {

    let tiles: [Route]
    let onOpen: (Route) -> Void

    /// `edit the bar` → Settings → App layout, which is the one thing a person
    /// can do about a grid they do not like.
    let onEditTheBar: () -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    /// Three per row (`HomeSections.kt:1035`), and **one per row at the
    /// accessibility sizes**: a three-column grid of 72-point tiles at AX5 is
    /// about 30 points of text width each, which is where a label gets
    /// clipped. `minimumScaleFactor` is the other answer and is rejected
    /// throughout this app.
    private var columns: [GridItem] {
        let count = typeSize.isAccessibilitySize ? 1 : 3
        return Array(repeating: GridItem(.flexible(), spacing: 8), count: count)
    }

    var body: some View {
        if !tiles.isEmpty {
            HomeCard(
                title: HomeEverythingStrings.title,
                actionLabel: HomeEverythingStrings.editTheBar,
                onAction: onEditTheBar
            ) {
                // A **lazy** grid, which is not the hazard ``HomeView``'s note
                // is about: that one is a lazy row holding a `Task`, and a
                // tile holds nothing — it is a `Button` whose action
                // navigates and returns. Thirteen tiles at most, and no fetch
                // follows any of them (map §7.6).
                LazyVGrid(columns: columns, spacing: 8) {
                    ForEach(tiles, id: \.self) { route in
                        tile(route)
                    }
                }
            }
        }
    }

    /// One tile: the route's symbol and its ``Route/label`` — drawn with its
    /// capitals, as Android draws `r.label`, and **`Rooms` for the route
    /// called `Satellites`**, which is the one place the name and the label
    /// diverge.
    ///
    /// ### No count badge, and that is a gap with a name
    ///
    /// Android draws a monospace badge in the top-right corner from
    /// `counts.forRoute(r)` — People, Calendar and Stations only. There is no
    /// counts source in `DomovoiKit`: nothing on iOS reads the three numbers,
    /// and inventing one would mean three more reads on a screen that already
    /// makes seven. A badge is a claim about how many of something there are,
    /// so a wrong one is worse than none. In `I1D3B-REPORT.md` as left undone.
    private func tile(_ route: Route) -> some View {
        Button { onOpen(route) } label: {
            VStack(spacing: 6) {
                Image(systemName: route.systemImage)
                    .font(.title3)
                    // The symbol is decoration beside the label, which says
                    // the same thing in words.
                    .accessibilityHidden(true)
                Text(route.label)
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.center)
            }
            .padding(8)
            // Android's `≥72 dp`, which is also over Apple's 44.
            .frame(maxWidth: .infinity, minHeight: 72)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
        // ### No synthetic label here, and the audit is why
        //
        // This read `.accessibilityLabel(route.label)`, which says the right
        // words and cost six audit findings — one per tile, each named
        // outright: `element: type=48 label='People' frame=(32.0, 699.3,
        // 104.3, 72.0)`, "Dynamic Type font sizes are partially unsupported".
        //
        // A label on the container **replaces** its children's accessibility,
        // so the element Apple inspected had a string and no `Text`. There
        // was no font to scale, which is exactly what that finding says, and
        // the `Text` below — `.caption`, `fixedSize`, free to grow — was
        // hidden behind the very label that described it.
        //
        // Dropping it changes nothing a person hears: the `Image` is
        // `accessibilityHidden`, so `Text(route.label)` is the only element
        // left and SwiftUI reads it as the button's label. Same words, and a
        // font the audit can see.
    }
}
