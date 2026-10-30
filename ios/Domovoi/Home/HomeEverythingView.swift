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
    private var perRow: Int { typeSize.isAccessibilitySize ? 1 : 3 }

    /// ``tiles`` in rows of ``perRow``.
    private var rows: [[Route]] {
        stride(from: 0, to: tiles.count, by: perRow).map { start in
            Array(tiles[start ..< min(start + perRow, tiles.count)])
        }
    }

    var body: some View {
        if !tiles.isEmpty {
            HomeCard(
                title: HomeEverythingStrings.title,
                actionLabel: HomeEverythingStrings.editTheBar,
                onAction: onEditTheBar
            ) {
                // ### Rows, not a `LazyVGrid` — and the grid was the bug
                //
                // This was `LazyVGrid(columns:)`, and it cost Home **six**
                // `Dynamic Type font sizes are partially unsupported`
                // findings, one per tile. Thirteen tiles at most and no fetch
                // behind any of them (map §7.6), so the laziness was never
                // earning its place; plain rows are both the fix and the
                // simpler shape.
                //
                // The audit's detail line is the part worth keeping, because
                // it is not about layout and six predictions died ignoring
                // it: *"User will not be able to change the font size of this
                // element."* Not clipping, not fitting, not width. The
                // elements came back as bare `staticText` leaves — `'Files'`
                // at 26.3 × 14.3 — advertising a font the audit could not see
                // scale, **inside a lazy container's cell**. Taken out of one,
                // the same `Text` with the same `.caption` audits clean.
                //
                // Five things it was **not**, each measured: the tiles' height
                // (`minHeight: 72` freed, still six, tiles 77–80 tall), the
                // synthetic `.accessibilityLabel` (removed, still six), the
                // column width (104 points → 329, still 100% of visible tiles
                // — see the note below), the hidden symbol, and
                // `.accessibilityAddTraits(.isButton)`.
                //
                // A short last row pads with clear tiles so that three tiles
                // and two tiles are the same width.
                if AuditStrip.active == .lazyGrid {
                    // The grid exactly as it was, reachable only by a launch
                    // argument that does not exist in a shipping build. It is
                    // here to be measured, not drawn: see ``AuditStrip``.
                    LazyVGrid(
                        columns: Array(
                            repeating: GridItem(.flexible(), spacing: 8),
                            count: perRow
                        ),
                        spacing: 8
                    ) {
                        ForEach(tiles, id: \.self) { route in tile(route) }
                    }
                } else {
                    VStack(spacing: 8) {
                        ForEach(rows, id: \.self) { row in
                            HStack(spacing: 8) {
                                ForEach(row, id: \.self) { route in
                                    tile(route)
                                }
                                ForEach(Array(0 ..< max(0, perRow - row.count)), id: \.self) { _ in
                                    Color.clear.frame(maxWidth: .infinity, minHeight: 72)
                                }
                            }
                        }
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
                    // Not `accessibilityHidden`, which the phone bar's tab
                    // does not do either. An `Image(systemName:)` with no
                    // label of its own contributes no words, so hiding it
                    // buys nothing and it changed no finding when tried.
                Text(route.label)
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.center)
            }
            .padding(8)
            // Android's `≥72 dp`, which is also over Apple's 44. **Measured
            // not to be the audit's objection**: dropping it for padding left
            // the finding count at six with the tiles 77 to 80 points tall.
            .frame(maxWidth: .infinity, minHeight: 72)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
        // ### These two lines did **not** fix the audit, and are kept anyway
        //
        // Written as a fix for the six
        // `Dynamic Type font sizes are partially unsupported` findings part 3b
        // put here, on the reasoning that every one came back `type=48` — a
        // bare **staticText** — while the phone bar's otherwise identical tab
        // carries these same two lines and audits clean. **Measured: still
        // six.** The cause was the `LazyVGrid` above; see its note.
        //
        // They stay because the reasoning was wrong about the audit and right
        // about the app. The tiles were exposed as static text, so VoiceOver
        // read six words with nothing to say any of them could be tapped.
        // `.isButton` is a true statement about a `Button`, and it would be
        // worth making with no audit in the building.
        //
        // Left as a record rather than deleted: the comment that originally
        // sat here argued these lines were the fix, which a later run
        // falsified. A comment explaining why something works is a claim to
        // check, not evidence.
        .accessibilityLabel(route.label)
        .accessibilityAddTraits(.isButton)
    }
}
