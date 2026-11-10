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
            notes
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

/// The profile's four numbers (`PersonDetail.kt:253-270`).
///
/// Two by two, and one column at an accessibility size. The session and turn
/// counts come from the **detail model**, so a failed read shows the rows it
/// really has rather than a number from somewhere else; the `"?"` rule lives
/// on the tab titles, which is where Android puts it too.
struct PersonStatGrid: View {

    let person: Person
    let detail: PersonDetailModel

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let tiles = [
            (
                PeopleStrings.statLastHeard,
                relTime(person.lastSeenAt, nowMs: Int64(Date().timeIntervalSince1970 * 1000)),
                PeopleStrings.presenceIs(person.presenceTier)
            ),
            (
                PeopleStrings.statVoiceProfiles,
                "\(person.voiceProfileCount)",
                PeopleStrings.enrolled
            ),
            (PeopleStrings.statSessions, "\(detail.sessionRows.count)", PeopleStrings.recent),
            (
                PeopleStrings.statTurns,
                "\(detail.turnRows.count)",
                PeopleStrings.conversationLogRows
            ),
        ]
        if typeSize.isAccessibilitySize {
            VStack(spacing: 10) {
                ForEach(Array(tiles.enumerated()), id: \.offset) { _, tile in
                    PersonStatTile(label: tile.0, value: tile.1, sub: tile.2)
                }
            }
        } else {
            VStack(spacing: 10) {
                HStack(spacing: 10) {
                    PersonStatTile(label: tiles[0].0, value: tiles[0].1, sub: tiles[0].2)
                    PersonStatTile(label: tiles[1].0, value: tiles[1].1, sub: tiles[1].2)
                }
                HStack(spacing: 10) {
                    PersonStatTile(label: tiles[2].0, value: tiles[2].1, sub: tiles[2].2)
                    PersonStatTile(label: tiles[3].0, value: tiles[3].1, sub: tiles[3].2)
                }
            }
        }
    }
}

/// One tile: what it measures, the number, and a line of detail.
///
/// **Stroked, not filled.** ``MusicStatTileView`` fills its tile with
/// `secondarySystemBackground` and puts `fgMuted` on it, and that pairing is
/// the one NIGHT-RULES §3 names: the token was measured against the *white*
/// canvas and fails Apple's audit on the darker surface. A border costs
/// nothing and keeps every label on the canvas it was measured on.
struct PersonStatTile: View {

    let label: String
    let value: String
    let sub: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            Text(value)
                .font(.title3.monospacedDigit())
                .fixedSize(horizontal: false, vertical: true)
            Text(sub)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.fgMuted.opacity(0.35), lineWidth: 1)
        )
        // One stop per tile: "voice profiles, 3, enrolled" rather than three
        // stops whose middle one is a bare number.
        .accessibilityElement(children: .combine)
    }
}
