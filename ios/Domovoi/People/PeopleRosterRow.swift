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
            if typeSize.isAccessibilitySize {
                // A row of three columns does not fit at AX3 and up, so it
                // becomes a column — the swap NIGHT-RULES §3 asks for, and
                // the one Android makes nowhere because a Compose `Row`
                // simply squeezes.
                VStack(alignment: .leading, spacing: 6) {
                    nameLine
                    subtitle
                    profiles
                }
                .frame(maxWidth: .infinity, alignment: .leading)
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
        .buttonStyle(.plain)
        .frame(minHeight: 56)
        .contentShape(.rect)
        .accessibilityLabel(person.name)
        .accessibilityValue(people.subtitle(for: person))
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
/// **The initial is dark on the two light gradients.** Android draws white on
/// all five; white on the amber (`#F2A618`) is about 2:1 and on the green
/// (`#54BF5C`) little better, which is under the 3:1 even a meaningful
/// graphic needs. The bubble is `accessibilityHidden`, so Apple's audit never
/// looks at it and nothing would have reported this — it is the same
/// measured-by-hand gap the health dots carry, answered here because an
/// initial is a glyph a person reads.
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
                    .foregroundStyle(Self.ink(personAvatarBucket(name)))
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

    /// Buckets 0 and 1 are the amber and the green — the two light fills.
    static func ink(_ bucket: Int) -> Color {
        bucket <= 1 ? Color.black.opacity(0.85) : .white
    }
}

/// The denylist card at the foot of the roster (`PeopleScreen.kt:227`).
///
/// It reads a route with **no auth dependency at all** (`denylist.py:22`), so
/// its count answers even for a phone whose household token the house has
/// forgotten — which is why it is drawn under a roster that may itself have
/// 401'd.
struct PeopleDenylistCard: View {

    let people: PeopleModel

    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 22

    var body: some View {
        Button {
            people.openDenylist()
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "mic.slash")
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .frame(width: glyphWidth, alignment: .center)
                VStack(alignment: .leading, spacing: 2) {
                    Text(PeopleStrings.denylistTitle)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    Text(PeopleStrings.denylistCardSub)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 4)
                Text("\(people.denylistCount)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Color.fgMuted)
            }
            .frame(minHeight: 56)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(PeopleStrings.denylistTitle)
        .accessibilityValue(PeopleStrings.optedOut(people.denylistCount))
    }
}
