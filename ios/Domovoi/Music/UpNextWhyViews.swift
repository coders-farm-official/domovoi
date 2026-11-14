import DomovoiKit
import SwiftUI

/// "Why this song?" on an Up next pick (1.3.0; Android `UpNextWhy.kt`, the
/// web's UpNextWhy): the pick's chip, and the server's sentence under the row.
/// The rules are ``WhyChip``'s.
///
/// With a `why`, the chip is a button of its own — never inside the row's
/// play button, so a tap on it shows or hides the line and never plays the
/// song. Without one it is the plain chip it always was.
struct UpNextWhyChip: View {

    let chip: WhyChip
    @Binding var open: Bool

    var body: some View {
        if chip.tappable {
            Button {
                open.toggle()
            } label: {
                HomeTonePill(label: chip.text, tone: .idle)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(chip.description)
            .accessibilityValue(open ? WhyChip.shown : WhyChip.hidden)
            .accessibilityHint(open ? WhyChip.hide : WhyChip.show)
        } else {
            HomeTonePill(label: chip.text, tone: .idle)
        }
    }
}

/// The server's sentence, exactly as it came, as one muted line under the row.
struct UpNextWhyLine: View {

    let text: String

    var body: some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 6))
    }
}

/// The chip and, while it is open, the line: what a pick row draws under its
/// title. Nothing for a row with no chip.
struct UpNextWhyBlock: View {

    let chip: WhyChip?
    @State private var open = false

    var body: some View {
        if let chip {
            VStack(alignment: .leading, spacing: 2) {
                UpNextWhyChip(chip: chip, open: $open)
                if let line = WhyChip.line(chip, open: open) {
                    UpNextWhyLine(text: line)
                }
            }
        }
    }
}
