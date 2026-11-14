import DomovoiKit
import SwiftUI

/// The voice denylist: a count, and the paragraph that explains why a count
/// is all there is.
///
/// `DenylistView` (`PeopleScreen.kt:307`). **Informational only** — no row
/// list, no delete. That is not a port taking a shortcut: the rows the route
/// returns carry an id, a timestamp and a note (`schemas.py:394`) and nothing
/// that names anybody, and the view's whole claim is that nothing
/// identifying is kept. Deleting one is `require_admin_mutation`
/// (`denylist.py:40`) and Android does not offer it either, so nothing is
/// lost by leaving it out.
struct PeopleDenylistView: View {

    let people: PeopleModel

    /// Inside Settings → People, which already scrolls (``PeopleView/embedded``).
    var embedded = false

    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 24

    var body: some View {
        PeopleScroll(embedded: embedded) {
            VStack(alignment: .leading, spacing: 18) {
                header
                Text(PeopleStrings.denylistExplainer)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                count
            }
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            // Android's is a `back` text button (`PeopleScreen.kt:314`), and
            // so is this: the three screens of this route are a branch rather
            // than a navigation stack, so there is no system back to inherit.
            HomeLink(PeopleStrings.back) { people.closeDenylist() }
            Text(PeopleStrings.denylistTitle)
                .font(.title3)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            HomeTonePill(label: PeopleStrings.optedOut(people.denylistCount), tone: .idle)
        }
    }

    /// The boxed number and its caption (`PeopleScreen.kt:355-362`).
    ///
    /// Drawn on a **stroked** surface rather than a filled one, like the
    /// profile's stat tiles and for the same measured reason: `fgMuted` was
    /// measured against the window canvas, and on
    /// `secondarySystemBackground` it fails Apple's audit (NIGHT-RULES §3).
    private var count: some View {
        HStack(spacing: 12) {
            Image(systemName: "mic.slash")
                .font(.body)
                .foregroundStyle(Color.fgMuted)
                .frame(width: glyphWidth, alignment: .center)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(people.denylistCount)")
                    .font(.title2.monospacedDigit())
                    .fixedSize(horizontal: false, vertical: true)
                Text(PeopleStrings.hashesOnFile)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(14)
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.fgMuted.opacity(0.35), lineWidth: 1)
        )
        // One stop: "2, hashes on file" rather than a bare number and then a
        // caption.
        .accessibilityElement(children: .combine)
    }
}
