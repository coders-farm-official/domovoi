import DomovoiKit
import SwiftUI

/// One person in the roster: who they are, when they were last heard, and how
/// many voice profiles the house has of them.
///
/// `RosterRow` (`PeopleScreen.kt:261`). The row's whole subtitle is
/// ``rosterSubtitle(_:nowMs:)`` — `"#3 · same name · 3d ago · high"` — so
/// that the `presence_tier ?: "high"` fallback has one home and a test can
/// assert the sentence rather than three fragments.
///
/// **The row reads as one stop.** The name is its label and the subtitle its
/// value, as ``FileRowView`` does: a VoiceOver pass over four people should
/// be four stops, not twelve, and the two dots carry nothing the subtitle
/// does not already say in words.
struct PersonRosterRow: View {

    let person: Person
    let people: PeopleModel

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        Button {
            people.select(person)
        } label: {
            rowBody
                // **Inside the label, not outside it.** A `Button`'s hit area
                // is its label's *content*, and this row's content is an
                // avatar, two lines of text and a `"3p"` with a `Spacer`
                // between them — so the whole middle of the row was a hole.
                // XCUITest taps an element's centre, which landed in that
                // hole: fourteen cases on train S002 failed with "the detail
                // did not open" while the roster sat there, because the tap
                // genuinely did nothing. `PeopleDenylistCard` had these two
                // modifiers inside its label all along, which is why it was
                // the one row on this screen that opened.
                .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(person.name)
        .accessibilityValue(people.subtitle(for: person))
    }

    @ViewBuilder private var rowBody: some View {
        if typeSize.isAccessibilitySize {
            // A row of three columns does not fit at AX3 and up, so it
            // becomes a column — the swap NIGHT-RULES §3 asks for, and the
            // one Android makes nowhere because a Compose `Row` simply
            // squeezes.
            VStack(alignment: .leading, spacing: 6) {
                nameLine
                subtitle
                profiles
            }
        } else {
            HStack(spacing: 10) {
                PersonAvatarBubble(name: person.name, size: 36)
                VStack(alignment: .leading, spacing: 2) {
                    nameLine
                    subtitle
                }
                Spacer(minLength: 4)
                profiles
            }
        }
    }

    private var nameLine: some View {
        HStack(spacing: 6) {
            Text(person.name)
                .font(.body)
                .lineLimit(typeSize.isAccessibilitySize ? nil : 1)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            // Heard in the last five minutes, and sharing a name. Both are
            // **also** in the subtitle in words (`3m ago`, `same name`), so
            // neither dot is the only carrier of its meaning.
            if people.isLive(person) { ToneDot(tone: .ok) }
            if person.namesakes > 0 { ToneDot(tone: .warn) }
        }
    }

    private var subtitle: some View {
        Text(people.subtitle(for: person))
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
    }

    /// `"3p"` (`PeopleScreen.kt:295`) — how many voice profiles are enrolled.
    private var profiles: some View {
        Text(people.profileCount(for: person))
            .font(.caption.monospacedDigit())
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// The name's initial over one of five gradients — `AvatarBubble`
/// (`Kit.kt:139`).
///
/// ### Two deliberate differences from Android
///
/// **The bucket is a written-out hash, not `hashValue`.** Android keys the
/// gradient on `abs(name.hashCode()) % 5`; Swift's `hashValue` is seeded per
/// process, so the same person would be a different colour on every launch.
/// ``personAvatarBucket(_:)`` is FNV-1a over the name's bytes — stable across
/// launches, devices and OS versions, which is the whole point of the
/// feature. A given name may land on a different colour than it does on
/// Android, and nothing depends on the two agreeing.
///
/// **The five fills are darkened.** Android draws a white initial on all
/// five, and on three of them that is between 2:1 and 3.3:1 — unreadable.
/// ``personAvatarGradients`` darkens each hue until white over it clears
/// 4.5:1, and the doc comment there has the measurements and why a dark ink
/// on the light ones was tried first and abandoned.
///
/// The bubble is `accessibilityHidden`, which is why Apple's audit reported
/// this with **`element: nil`** and no name attached: a hidden view's
/// unreadable text is a finding nobody can attribute. Three of them stood
/// against this screen on train S002.
///
/// The diameter is `@ScaledMetric`, never a fixed `frame`: a 36-point circle
/// beside text that has grown to AX5 is the mistake ``FileRowView``'s icon
/// column was fixed for, and the picture from the Simulator was the only
/// thing that found it.
struct PersonAvatarBubble: View {

    let name: String
    /// The diameter at the ordinary type size — 36 in the roster, 28 in the
    /// detail header, 56 on the profile tab, as Android's three call sites
    /// pass.
    let size: CGFloat

    /// One scale factor for every diameter, so the three sites cannot drift.
    @ScaledMetric(relativeTo: .body) private var scale: CGFloat = 1

    var body: some View {
        let diameter = size * scale
        let pair = personAvatarGradients[personAvatarBucket(name)]
        Circle()
            .fill(
                LinearGradient(
                    colors: [Self.colour(pair.from), Self.colour(pair.to)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .frame(width: diameter, height: diameter)
            .overlay(
                Text(personAvatarInitial(name))
                    .font(.footnote.weight(.semibold))
                    // White on all five, as Android has it — the fills are
                    // what moved, so one ink is right everywhere.
                    .foregroundStyle(.white)
            )
            // Decorative: the name is beside it, and "letter K in a circle" is
            // noise in a VoiceOver pass.
            .accessibilityHidden(true)
    }

    /// A 24-bit RGB from ``personAvatarGradients`` as a `Color`. The package
    /// holds hexes because `DomovoiKit` imports no SwiftUI.
    static func colour(_ rgb: UInt32) -> Color {
        Color(
            red: Double((rgb >> 16) & 0xFF) / 255,
            green: Double((rgb >> 8) & 0xFF) / 255,
            blue: Double(rgb & 0xFF) / 255
        )
    }
}

/// The denylist card at the foot of the roster (`PeopleScreen.kt:227`).
///
/// It reads a route with **no auth dependency at all** (`denylist.py:22`), so
/// its count answers even for a phone whose household token the house has
/// forgotten — which is why it is drawn under a roster that may itself have
/// 401'd.
///
/// It swaps to a column at an accessibility type size for the reason
/// ``PersonRosterRow`` does. It did not, at first, and it was **the only
/// thing on this screen Apple's audit named**: all three of its labels came
/// back `Dynamic Type font sizes are partially unsupported` on train S002
/// while the four roster rows above it — same fonts, same `.fixedSize` — came
/// back clean. A glyph column, a text column and a count squeezed into one
/// `HStack` leave the text no width to grow into; the rows that swap have no
/// such finding.
struct PeopleDenylistCard: View {

    let people: PeopleModel

    @Environment(\.dynamicTypeSize) private var typeSize

    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 22

    var body: some View {
        Button {
            people.openDenylist()
        } label: {
            cardBody
                .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(PeopleStrings.denylistTitle)
        .accessibilityValue(PeopleStrings.optedOut(people.denylistCount))
    }

    @ViewBuilder private var cardBody: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 10) {
                    glyph
                    title
                }
                subtitle
                count
            }
        } else {
            HStack(spacing: 10) {
                glyph
                VStack(alignment: .leading, spacing: 2) {
                    title
                    subtitle
                }
                Spacer(minLength: 4)
                count
            }
        }
    }

    private var glyph: some View {
        Image(systemName: "mic.slash")
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .frame(width: glyphWidth, alignment: .center)
    }

    private var title: some View {
        Text(PeopleStrings.denylistTitle)
            .font(.subheadline)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
    }

    private var subtitle: some View {
        Text(PeopleStrings.denylistCardSub)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
    }

    private var count: some View {
        Text("\(people.denylistCount)")
            .font(.caption.monospacedDigit())
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}
