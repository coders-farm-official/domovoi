import DomovoiKit
import SwiftUI

/// Health's titled card — `Section(title, sub)` (`ContainersScreen.kt:303`).
///
/// ### Why this is not ``HomeCard``
///
/// Three differences, and each of them is structural rather than cosmetic:
///
///  * the right-hand slot is a **quiet count** (`2 open`,
///    `everything else keeps working`), where ``HomeCard``'s is a
///    ``HomeLink`` — a control. A count drawn as a link is a control that
///    does nothing, which is the owner's no-misleading-controls rule;
///  * there is a **divider** under the header, and the rows run edge to
///    edge under it. ``HomeCard`` pads its content by 16 on every side, so a
///    row's own divider would stop short of the card's edges;
///  * the rows carry their own padding (`CheckRow`'s 16 × 10,
///    `IncidentRow`'s 16 × 12), which is Android's `padding = 0` on the card.
///
/// The **fill is the same** (`.quaternary.opacity(0.4)` at radius 16), so
/// this does not introduce a second card look — only a second card
/// *structure*, which is what Android has.
struct HealthSectionCard<Content: View>: View {

    let title: String

    /// The quiet right-hand half: `2 open`, `everything else keeps working`,
    /// or nil for a part's card, which has none (`:140`).
    let count: String?

    let content: Content

    init(title: String, count: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.count = count
        self.content = content()
    }

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            // `HorizontalDivider(color = borderSoft)` (`:321`).
            Divider()
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
    }

    /// `heightIn(min = 44.dp).padding(horizontal = 16.dp)` (`:306`).
    ///
    /// **A column at an accessibility size**, the shape
    /// ``HomeAttentionView/rowBody(_:)`` uses: `containers that need
    /// attention` and `2 open` sharing one row at AX5 leaves the title about
    /// four characters wide, and `minimumScaleFactor` is rejected everywhere
    /// in this app.
    @ViewBuilder private var header: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) {
                titleText
                if let count { countText(count) }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                titleText
                Spacer(minLength: 8)
                if let count { countText(count) }
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
        }
    }

    private var titleText: some View {
        Text(title)
            .font(.headline)
            // A card title is a heading, so VoiceOver's heading rotor reaches
            // it and the rows under it read as belonging to it — the same
            // trait ``HomeSectionHeader`` carries.
            .accessibilityAddTraits(.isHeader)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
    }

    /// `labelMedium` in `fgSubtle` on Android. **``Color/fgMuted`` here**:
    /// this app has no `fgSubtle` token, and the one it has was measured
    /// (7.44:1 light, 7.88:1 dark) where a third, lighter ink would be a
    /// colour nobody has measured. DECISIONS, "there is no fgSubtle".
    private func countText(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
    }
}

/// A row of the card's content, with the divider Android draws **between**
/// rows and not above the first (`:294`, `:138-143`).
///
/// A view rather than a line in each caller because three different row kinds
/// want it — checks, open incidents, resolved incidents — and "if it is not
/// the first one" written three times is how one of them comes to draw a
/// divider under the header that already has one.
struct HealthRowDivider: View {

    let isFirst: Bool

    var body: some View {
        if !isFirst { Divider() }
    }
}
