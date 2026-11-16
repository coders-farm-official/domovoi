import DomovoiKit
import SwiftUI

/// Which of the four tabs is open (`PersonDetail.kt:111-119`).
///
/// An enum rather than Android's `Int`, so that the branch below cannot read
/// the titles and the content off two different indexes.
enum PersonDetailTab: Int, CaseIterable, Identifiable {
    case profile
    case memory
    case sessions
    case conversations

    var id: Int { rawValue }
}

/// One person: the four tabs, and the profile that is the first of them.
///
/// `PersonDetail` (`PersonDetail.kt:66`) with its `ScrollableTabRow`, minus
/// the two controls a phone cannot honour — see ``PeopleView``'s heading for
/// `rename` and `forget person`, and ``PeoplePath`` for the tiers that decide
/// it.
///
/// The outer stack is a plain `VStack`: the tab views below hold which
/// session is expanded and which long answer is open, and state held inside a
/// `LazyVStack`'s child is state that can be recycled out from under the
/// reader. The lists **inside** each tab are lazy, which is where laziness
/// pays.
struct PersonDetailView: View {

    let person: Person
    let people: PeopleModel
    let detail: PersonDetailModel

    @Binding var forgetting: PersonForgetAsk?

    /// Inside Settings → People, which already scrolls (``PeopleView/embedded``).
    var embedded = false

    /// "This phone" and "listening history" (2026-11-12), nil while the
    /// screen has not built it.
    var thisPhone: PeopleThisPhoneModel?

    /// The shell, for the person's own music mix (Up next 1.3.0); nil draws
    /// none.
    var shell: ShellModel?

    /// The roster, re-read after a claim that changed who has a phone.
    var onPeopleChanged: @MainActor () -> Void = {}

    @State private var tab: PersonDetailTab = .profile

    var body: some View {
        PeopleScroll(embedded: embedded) {
            VStack(alignment: .leading, spacing: 14) {
                header
                PersonTabBar(titles: detail.tabTitles, tab: $tab)
                Divider()
                content
            }
        }
        // **The five reads start here**, not in the row that was tapped: a
        // `Task` begun in a lazy row dies with the row, and
        // ``PersonDetailModel/show(_:)`` is a no-op for the person already on
        // screen, so arriving twice costs nothing.
        .onAppear { detail.show(person) }
        // **Keyed on the person**, so opening somebody else starts on their
        // profile rather than on the tab the last person was left on —
        // Android's `remember(person.id)` (`PersonDetail.kt:77`). The id, not
        // the value: a push that moves `last_seen_at` must not throw the
        // reader back to the profile tab, and ``PeopleModel/selected`` is
        // looked up fresh on every roster read.
        .onChange(of: person.id) { _, _ in
            tab = .profile
            detail.show(person)
        }
        // A room may just have learned their voice: the same person with a
        // new sample count re-reads the rooms that have learned it
        // (`LaunchedEffect(personId, tick, voiceSamples)`, `PeopleModels.kt:320`).
        // ``PersonDetailModel/show(_:)`` re-reads only the rooms for that.
        .onChange(of: person.voiceProfileCount) { _, _ in
            detail.show(person)
        }
    }

    // MARK: - who this is

    /// Back, the avatar, the name, and a `live` pill when they are in the
    /// room (`PersonDetail.kt:84-106`).
    private var header: some View {
        HStack(spacing: 8) {
            HomeLink(PeopleStrings.back) {
                people.closeDetail()
                detail.close()
            }
            PersonAvatarBubble(name: person.name, size: 28)
            Text(person.name)
                .font(.title3)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Spacer(minLength: 8)
            if people.isLive(person) {
                HomeTonePill(label: PeopleStrings.live, tone: .ok)
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch tab {
        case .profile:
            profile
        case .memory:
            PersonMemoryTabView(detail: detail, forgetting: $forgetting)
        case .sessions:
            PersonSessionsTabView(detail: detail)
        case .conversations:
            PersonConversationsTabView(detail: detail)
        }
    }

    // MARK: - the profile tab

    /// `ProfileTab` (`PersonDetail.kt:199`) without its two buttons, and with
    /// one sentence where they were.
    private var profile: some View {
        VStack(alignment: .leading, spacing: 16) {
            identity
            if let note = namesakeNote(person) {
                namesake(note)
            }
            // Not recognized by voice yet: between the same-name note and the
            // header tiles, as Android and the web order it (`PersonDetail.kt:267-268`).
            if let relearn = PeopleVoiceId.personNotice(person, voiceId: people.voiceId) {
                PeopleVoiceNoticeView(notice: relearn, wash: .warn)
                    .accessibilityIdentifier("person.voiceRelearnNotice")
            }
            PersonStatGrid(person: person, detail: detail)
            // Where renaming and forgetting are done. Said once, under the
            // tiles, exactly where Android draws the two controls — so a
            // reader who went looking for them finds the answer rather than
            // concluding the app forgot. ``PeopleStrings/renamingIsOnTheDashboard``
            // is one of this screen's two iOS-only sentences
            // (`overnight/people/IOS-ONLY-STRINGS.md`).
            Text(PeopleStrings.renamingIsOnTheDashboard)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // Rooms that have learned this voice (1.3.0), after the stats and before
            // the notes (`PersonDetail.kt:289`). Hidden when there are none,
            // while they relearn, and while voice ID is off.
            PersonVoiceRoomsSection(person: person, rooms: detail.voiceRooms, voiceId: people.voiceId)
            notes
            // Whose this phone is, and their listening history
            // (`PersonDetail.kt` ProfileTab, 2026-11-12).
            if let thisPhone {
                Divider()
                PersonThisPhoneSection(person: person, model: thisPhone, onPeopleChanged: onPeopleChanged)
            }
            // Their own music mix (1.3.0), after listening history: theirs
            // to change from their phone (`PersonDetail.kt` ProfileTab).
            if let shell {
                PersonMusicMixSection(model: shell, personID: person.id, name: person.name)
            }
        }
    }

    private var identity: some View {
        HStack(alignment: .top, spacing: 14) {
            PersonAvatarBubble(name: person.name, size: 56)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(person.name)
                        .font(.title2)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    if people.isLive(person) {
                        HomeTonePill(label: PeopleStrings.live, tone: .ok)
                    }
                    if person.namesakes > 0 {
                        HomeTonePill(label: PeopleStrings.sameName, tone: .warn)
                    }
                }
                Text(
                    PeopleStrings.personLine(
                        id: person.id,
                        added: relTime(person.createdAt, nowMs: nowMs)
                    )
                )
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
    }

    /// The amber wash over the namesake note (`PersonDetail.kt:238-251`).
    ///
    /// The note's last sentence is **not** Android's: theirs ends *"If they
    /// are different people, rename one of them"*, which is advice about a
    /// control this screen does not have
    /// (``PeopleStrings/namesakeFixIsOnTheDashboard``).
    private func namesake(_ note: String) -> some View {
        Text(note)
            .font(.footnote)
            .fixedSize(horizontal: false, vertical: true)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color.warnInk.opacity(0.12))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(Color.warnInk.opacity(0.35), lineWidth: 1)
            )
    }

    private var notes: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(PeopleStrings.notes)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            // A blank string counts as no notes, as Android's
            // `takeIf { it.isNotBlank() }` does (`PersonDetail.kt:275`): a
            // row whose column holds a space is not a note.
            let written = person.notes?.trimmingCharacters(in: .whitespacesAndNewlines)
            Text((written?.isEmpty == false ? written : nil) ?? PeopleStrings.noNotesYet)
                .font(.subheadline)
                .foregroundStyle(written?.isEmpty == false ? Color.primary : Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The clock the two relative times on this tab are measured against.
    ///
    /// Read once per body, not per `Text`, so `added 2d ago` and the
    /// `last heard` tile cannot be a frame apart.
    private var nowMs: Int64 { Int64(Date().timeIntervalSince1970 * 1000) }
}

/// The four tabs (`ScrollableTabRow`, `PersonDetail.kt:120`).
///
/// It scrolls sideways at ordinary sizes and becomes a column at an
/// accessibility size: `conversations (200)` alone is wider than a phone at
/// AX5, and four of them in a row would each be squeezed to a character — the
/// shape `minimumScaleFactor` is usually reached for, which is rejected
/// throughout this app.
struct PersonTabBar: View {

    let titles: [String]
    @Binding var tab: PersonDetailTab

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(PersonDetailTab.allCases) { which in button(which) }
            }
        } else {
            ScrollView(.horizontal) {
                HStack(spacing: 14) {
                    ForEach(PersonDetailTab.allCases) { which in button(which) }
                }
            }
            .scrollIndicators(.hidden)
        }
    }

    private func button(_ which: PersonDetailTab) -> some View {
        let selected = which == tab
        return Button {
            tab = which
        } label: {
            Text(title(which))
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
                .frame(minHeight: 44, alignment: .center)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // `brandInk` for the open tab, never ``Color/brand``: 5.65:1 against
        // 2.05:1, and a tab title is text.
        .foregroundStyle(selected ? Color.brandInk : Color.fgMuted)
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : [.isButton])
    }

    /// The title the model wrote, with the raw word as a fallback.
    ///
    /// Indexed by the case's `rawValue`, which is the one thing that keeps the
    /// titles and the content in step; a short array is a model that has not
    /// answered rather than a reason to crash.
    private func title(_ which: PersonDetailTab) -> String {
        titles.indices.contains(which.rawValue) ? titles[which.rawValue] : ""
    }
}

/// The profile header: where they were last heard, and when (owner
/// decision, 1.3.0; the web People page's `ProfileTab`, 838da8b6).
///
/// "last heard in" on the left is the room of their newest conversation row
/// (``PersonDetailModel/lastHeardIn``): "the phone" for a phone's chat mic,
/// "chat" for a turn with no room, "—" before they have said anything. "last
/// heard" on the right is how long ago, with their presence under it. The
/// voice profiles, sessions and turns tiles are gone.
///
/// Side by side, and one column at an accessibility size.
struct PersonStatGrid: View {

    let person: Person
    let detail: PersonDetailModel

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let heardIn = PersonStatTile(label: PeopleStrings.statLastHeardIn, value: detail.lastHeardIn)
            .accessibilityIdentifier("person.lastHeardIn")
        let heard = PersonStatTile(
            label: PeopleStrings.statLastHeard,
            value: relTime(person.lastSeenAt, nowMs: Int64(Date().timeIntervalSince1970 * 1000)),
            sub: PeopleStrings.presenceIs(person.presenceTier)
        )
        .accessibilityIdentifier("person.lastHeard")
        if typeSize.isAccessibilitySize {
            VStack(spacing: 10) {
                heardIn
                heard
            }
            .fixedSize(horizontal: false, vertical: true)
        } else {
            // Equal heights, so the two borders line up although only the
            // right tile has a third line.
            HStack(alignment: .top, spacing: 10) {
                heardIn
                heard
            }
            .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// One tile: what it measures, the value, and an optional line of detail.
///
/// **Stroked, not filled.** ``MusicStatTileView`` fills its tile with
/// `secondarySystemBackground` and puts `fgMuted` on it, and that pairing is
/// the one NIGHT-RULES §3 names: the token was measured against the *white*
/// canvas and fails Apple's audit on the darker surface. A border costs
/// nothing and keeps every label on the canvas it was measured on.
struct PersonStatTile: View {

    let label: String
    let value: String
    var sub: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            Text(value)
                .font(.title3.monospacedDigit())
                .fixedSize(horizontal: false, vertical: true)
            if let sub {
                Text(sub)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.fgMuted.opacity(0.35), lineWidth: 1)
        )
        // One stop per tile: "last heard, 2m ago, presence: high" rather
        // than three stops whose middle one is a bare value.
        .accessibilityElement(children: .combine)
    }
}

/// Rooms that have learned this voice (`VoiceRoomsSection`, `PersonDetail.kt:310-343`;
/// the web People page's `PersonVoiceRooms`). **Read-only**: rooms never use a
/// phone's samples, so the phone records nothing; the hint says how the
/// person teaches a room themselves.
///
/// Draws nothing when the house has no rooms, the server is older than the
/// route, the person has to introduce themselves again, or voice ID is off
/// (``PersonVoiceRooms/shows(_:person:voiceId:)``).
struct PersonVoiceRoomsSection: View {

    let person: Person
    let rooms: [PersonVoiceRoom]
    let voiceId: VoiceIdStatus?

    private var name: String { person.name }

    var body: some View {
        if PersonVoiceRooms.shows(rooms, person: person, voiceId: voiceId) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(PeopleStrings.voiceRoomsLabel)
                        .font(.headline)
                        .accessibilityAddTraits(.isHeader)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(PersonVoiceRooms.count(rooms))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                // Wrapping, not scrolling: a horizontal scroll hides what it
                // cannot fit without saying so (Android's `FlowRow`).
                PersonVoiceRoomsFlow(spacing: 6) {
                    ForEach(rooms) { room in
                        HomeTonePill(label: PersonVoiceRooms.chip(room), tone: room.known ? .ok : .idle)
                    }
                }
                if let hint = PersonVoiceRooms.hint(name: name, rooms: rooms) {
                    Text(hint)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// Left-to-right rows that wrap, for the room chips (Compose's `FlowRow`).
///
/// A chip wider than the row gets the row's width and wraps its own text,
/// so nothing is clipped at an accessibility size.
struct PersonVoiceRoomsFlow: Layout {

    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        let frames = place(subviews, width: width)
        let used = frames.reduce(CGSize.zero) { size, frame in
            CGSize(width: max(size.width, frame.maxX), height: max(size.height, frame.maxY))
        }
        return CGSize(width: proposal.width ?? used.width, height: used.height)
    }

    func placeSubviews(
        in bounds: CGRect,
        proposal: ProposedViewSize,
        subviews: Subviews,
        cache: inout ()
    ) {
        let frames = place(subviews, width: bounds.width)
        for (subview, frame) in zip(subviews, frames) {
            subview.place(
                at: CGPoint(x: bounds.minX + frame.minX, y: bounds.minY + frame.minY),
                anchor: .topLeading,
                proposal: ProposedViewSize(width: frame.width, height: frame.height)
            )
        }
    }

    private func place(_ subviews: Subviews, width: CGFloat) -> [CGRect] {
        var frames: [CGRect] = []
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        for subview in subviews {
            var size: CGSize = subview.sizeThatFits(.unspecified)
            if size.width > width {
                size = subview.sizeThatFits(ProposedViewSize(width: width, height: nil))
                size.width = min(size.width, width)
            }
            if x > 0, x + size.width > width {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            frames.append(CGRect(x: x, y: y, width: size.width, height: size.height))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return frames
    }
}
