import DomovoiKit
import SwiftUI

/// The phone's own "up next" section (P9; Android `PhoneUpNextUi.kt`,
/// 4fb53334; the web's PlayerUpNextHere), under the explicit queue rows of
/// the player tab's Queue panel: what the mix is built from, the Autoplay
/// switch, "for" whom (two people or more), one muted line, then each pick
/// still to come with why it was picked, "less like this", remove, and tap
/// to play it now. No new screen (owner).
///
/// No misleading controls: the caller draws this only when
/// ``MusicLocalQueueModel/upNextShown`` — never for an unpaired app, a queue
/// with no library song, a play the server refused Up next for, or while
/// casting (the room's own Up next shows in its queue then).
///
/// The words and the switch's name are the room queue's
/// (``RoomUpNextStrings``) and Android's (``PhoneUpNextStrings``).
struct PhoneUpNextSection: View {

    let player: MusicLocalQueueModel

    static let headID = "player-upnext-head"
    static let autoplayID = "player-upnext-autoplay"
    static let lineID = "player-upnext-line"
    static let forID = "player-upnext-for"

    var body: some View {
        let rows = player.pickRows
        VStack(alignment: .leading, spacing: 4) {
            head
            // **Eager, not lazy.** The picks waiting are a batch or two (the
            // phone asks for more only at five left), and they sit at the foot
            // of the page: a `LazyVStack` there builds nothing until it is
            // scrolled onto the screen, so VoiceOver and the UI cases found a
            // head with no picks under it (train I041).
            VStack(alignment: .leading, spacing: 4) {
                ForEach(rows, id: \.self) { index in
                    if player.queue.indices.contains(index) {
                        PhoneUpNextRow(player: player, item: player.queue[index], index: index)
                            .id("up-next-\(index)-\(player.queue[index].uid)")
                    }
                }
            }
        }
        .task(id: player.upNext?.mayAsk == true) {
            await player.upNext?.readPeople()
        }
    }

    private var head: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .center, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(PhoneUpNextStrings.head)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Color.fgMuted)
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityIdentifier(Self.headID)
                    if !player.upNextBasis.isEmpty {
                        Text(player.upNextBasis)
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 8)
                Toggle(PhoneUpNextStrings.autoplay, isOn: autoplay)
                    .font(.footnote)
                    .fixedSize()
                    .accessibilityLabel(PhoneUpNextStrings.autoplayLabel)
                    .accessibilityIdentifier(Self.autoplayID)
            }
            .frame(minHeight: 44)
            if let upNext = player.upNext,
               phoneUpNextForShown(people: upNext.people.count, state: upNext.state) {
                forSelector(upNext)
            }
            if let line = player.upNextLine {
                Text(line)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier(Self.lineID)
            }
        }
        .padding(.top, 8)
        .padding(.bottom, 4)
    }

    private var autoplay: Binding<Bool> {
        Binding(
            get: { player.upNext?.autoplay ?? true },
            set: { player.setUpNextAutoplay($0) }
        )
    }

    /// "for [household | names]": whose taste the mix follows.
    private func forSelector(_ upNext: PhoneUpNext) -> some View {
        let people = upNext.people
        let who: String = {
            guard let person = upNext.state?.person else { return PhoneUpNextStrings.household }
            return people.first(where: { $0.id == person.id })?.name ?? person.name ?? PhoneUpNextStrings.household
        }()
        return HStack(spacing: 6) {
            Text(PhoneUpNextStrings.forWord)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
            Menu {
                Button(PhoneUpNextStrings.household) { player.setUpNextListener(nil) }
                ForEach(people, id: \.id) { person in
                    Button(person.name ?? PhoneUpNextStrings.personFallback(person.id)) {
                        player.setUpNextListener(person.id)
                    }
                }
            } label: {
                HStack(spacing: 2) {
                    Text(who)
                        .font(.footnote)
                        .fixedSize(horizontal: false, vertical: true)
                    Image(systemName: "chevron.down")
                        .font(.caption2)
                        .accessibilityHidden(true)
                }
                .frame(minHeight: 44)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            .accessibilityLabel(PhoneUpNextStrings.forLabel(who))
            .accessibilityIdentifier(Self.forID)
        }
    }
}

/// One pick still to come: tap = play it now; less like this; remove.
///
/// **A lazy row holds no `Task`**: every write is the model's.
struct PhoneUpNextRow: View {

    let player: MusicLocalQueueModel
    let item: MusicPlayerItem
    let index: Int

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                play
                // Why it was picked: a muted chip ("same artist", "90s"); with
                // the server's sentence ("why this song?", 1.3.0) a button of
                // its own, OUTSIDE the play button, that shows the sentence
                // under the row and never plays the song.
                UpNextWhyBlock(chip: WhyChip.phone(item))
            }
            if player.mayLessLike(index) {
                control("hand.thumbsdown", RoomUpNextStrings.lessLikeLabel(item.artist), tint: .fgMuted) {
                    player.lessLike(index)
                }
            }
            control("xmark", PhoneUpNextStrings.removeLabel(item.title), tint: .errInk) {
                player.engine?.remove(at: index)
            }
        }
        .padding(.vertical, 2)
    }

    /// Tap = play it now.
    private var play: some View {
        Button {
            player.tapped(index)
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Image(systemName: "sparkles")
                        .font(.caption2)
                        .foregroundStyle(Color.fgMuted)
                        .accessibilityLabel(PhoneUpNextStrings.pick)
                    Text(item.title)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text(item.artist.flatMap { $0.isEmpty ? nil : $0 } ?? "—")
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .contentShape(.rect)
            .accessibilityElement(children: .combine)
        }
        .buttonStyle(.plain)
        .accessibilityHint(PhoneUpNextStrings.playNow)
        .accessibilityIdentifier("player-upnext-pick-\(item.upNextSeq ?? -1)")
    }

    private func control(
        _ symbol: String,
        _ label: String,
        tint: Color,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.caption)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(tint)
        .accessibilityLabel(label)
    }
}
