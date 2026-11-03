import DomovoiKit
import SwiftUI

/// The sessions tab: every time they have spoken, and what was said in each.
///
/// `SessionsTab` (`PersonTabs.kt:456`). A row expands into **that session's**
/// turns, filtered client-side by `session_id`
/// (``PersonDetailModel/turns(in:)``) because there is no per-session turns
/// route — so a session older than the conversations read's `limit=200` has
/// none in hand, and says so (``PeopleStrings/noRowsInSession``) rather than
/// drawing nothing.
///
/// Which rows are open is held **here**, not in the row: the rows are in a
/// `LazyVStack` and a recycled row's `@State` goes back to its default, which
/// would close a session the reader had just opened.
struct PersonSessionsTabView: View {

    let detail: PersonDetailModel

    @State private var open: Set<String> = []

    var body: some View {
        if detail.sessionsTabIsLoading {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
                .accessibilityLabel(PeopleStrings.statSessions)
        } else if let why = detail.errors.sessions {
            // F-A008 again: a failed read is the error card, never
            // "hasn't had a session yet".
            PersonErrorCard(why: why) { detail.refresh() }
        } else if let note = detail.sessionsEmptyNote {
            PersonEmptyNote(title: note.title, detail: note.detail)
        } else {
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(detail.sessionRows) { session in
                    PersonSessionRow(
                        session: session,
                        detail: detail,
                        open: open.contains(session.id),
                        onToggle: {
                            if open.contains(session.id) {
                                open.remove(session.id)
                            } else {
                                open.insert(session.id)
                            }
                        }
                    )
                    Divider()
                }
            }
        }
    }
}

/// One session, and its turns when it is open — `SessionRowView`
/// (`PersonTabs.kt:482`).
struct PersonSessionRow: View {

    let session: PersonSession
    let detail: PersonDetailModel
    let open: Bool
    let onToggle: () -> Void

    @Environment(\.dynamicTypeSize) private var typeSize
    @ScaledMetric(relativeTo: .body) private var chevronWidth: CGFloat = 20

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button(action: onToggle) {
                head
                    // Inside the label, for ``PersonRosterRow``'s measured
                    // reason: the `Spacer` between the time and the chip is
                    // not content, so with these two modifiers outside the
                    // `Button` the middle of the row — where a tap lands —
                    // was not a tap target at all.
                    .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(detail.sessionTime(session))
            .accessibilityValue(detail.sessionSubtitle(session))
            .accessibilityAddTraits(open ? [.isButton, .isSelected] : [.isButton])
            if open {
                turns
            }
        }
    }

    /// The row itself: a column at an accessibility size, where a chevron, a
    /// time, a room chip and two counts have no business sharing a line.
    @ViewBuilder private var head: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 4) {
                when
                PersonRoomChip(roomID: session.roomID)
                counts
            }
        } else {
            HStack(spacing: 10) {
                Image(systemName: open ? "chevron.down" : "chevron.right")
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .frame(width: chevronWidth, alignment: .center)
                when
                Spacer(minLength: 4)
                PersonRoomChip(roomID: session.roomID)
                counts
            }
        }
    }

    private var when: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(detail.sessionTime(session))
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            // `"aaaa1111 · 2:00"` — the short id and how long it ran. The
            // duration is an em dash for a session the server sent one
            // timestamp for: a duration that was not measured is not a
            // duration of zero (``PersonSession/durationSec``).
            Text(detail.sessionSubtitle(session))
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
    }

    private var counts: some View {
        let meta = detail.sessionMeta(session)
        return VStack(alignment: typeSize.isAccessibilitySize ? .leading : .trailing, spacing: 2) {
            Text(meta.turns)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            Text(meta.last)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var turns: some View {
        let rows = detail.turns(in: session)
        if rows.isEmpty {
            Text(PeopleStrings.noRowsInSession)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, 18)
                .padding(.vertical, 8)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(rows) { turn in
                    PersonTurnBrief(turn: turn, detail: detail)
                    Divider()
                }
            }
            .padding(.leading, 18)
        }
    }
}

/// One turn inside an expanded session — `TurnBrief` (`PersonTabs.kt:556`).
struct PersonTurnBrief: View {

    let turn: PersonTurn
    let detail: PersonDetailModel

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 8) {
                Text(detail.turnTime(turn))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                HomeTonePill(
                    label: turn.handlerLabel,
                    tone: turn.matchedAHandler ? .ok : .idle
                )
                Text(turn.matchedPath ?? "")
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            Text(PeopleStrings.quoted(turn.userText))
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Text(turn.assistantText ?? "")
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The conversations tab: the whole log, with a search over it.
///
/// `ConversationsTab` (`PersonTabs.kt:606`). The search reads **both** halves
/// of a turn — what they said and what Domovoi said
/// (``PersonDetailModel/filteredTurns``) — and the count beside it is the
/// filtered number over the **unfiltered** total, which is what makes
/// `1 of 4` worth reading.
///
/// Which long answers are expanded is held here, for
/// ``PersonSessionsTabView``'s reason.
struct PersonConversationsTabView: View {

    let detail: PersonDetailModel

    @State private var expanded: Set<Int> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            searchRow
            if detail.conversationsTabIsLoading {
                ProgressView()
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 24)
                    .accessibilityLabel(PeopleStrings.statTurns)
            } else if let why = detail.errors.conversations {
                PersonErrorCard(why: why) { detail.refresh() }
            } else if let note = detail.conversationsEmptyNote {
                PersonEmptyNote(title: note.title, detail: note.detail)
            } else {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(detail.filteredTurns) { turn in
                        PersonTurnCard(
                            turn: turn,
                            detail: detail,
                            expanded: expanded.contains(turn.id),
                            onToggle: {
                                if expanded.contains(turn.id) {
                                    expanded.remove(turn.id)
                                } else {
                                    expanded.insert(turn.id)
                                }
                            }
                        )
                        Divider()
                    }
                }
            }
        }
    }

    /// The field and `"1 of 4"` (`PersonTabs.kt:624-656`).
    ///
    /// Single-line on Android, so it keeps a `submitLabel(.search)`.
    private var searchRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            TextField(
                PeopleStrings.conversationSearchPlaceholder,
                text: Binding(get: { detail.query }, set: { detail.typedQuery($0) })
            )
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .submitLabel(.search)
            .accessibilityLabel(PeopleStrings.conversationSearchPlaceholder)
            Text(detail.conversationCount)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// One turn on the conversations tab — `TurnCard` (`PersonTabs.kt:676`).
///
/// The answer is cut at 130 characters when it is longer than 140, with
/// `more` / `less` (``personAnswerText(_:expanded:)``). The two numbers are
/// Android's and are not an off-by-one: opening a barely-long answer should
/// reveal more than an ellipsis.
struct PersonTurnCard: View {

    let turn: PersonTurn
    let detail: PersonDetailModel
    let expanded: Bool
    let onToggle: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(detail.turnTime(turn))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                PersonRoomChip(roomID: turn.roomID)
                HomeTonePill(
                    label: turn.handlerLabel,
                    tone: turn.matchedAHandler ? .ok : .idle
                )
                Spacer(minLength: 0)
                Text(PeopleStrings.turnID(turn.id))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Color.fgMuted)
            }
            Text(PeopleStrings.quoted(turn.userText))
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Text(personAnswerText(turn.assistantText, expanded: expanded))
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            if personAnswerIsLong(turn.assistantText) {
                HomeLink(expanded ? PeopleStrings.less : PeopleStrings.more, action: onToggle)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Which room a turn or a session happened in — `RoomChip`
/// (`Kit.kt:119`), whose fallback is an em dash.
///
/// The dot is the brand **fill** on Android; here it is `brandInk`, which is
/// the token for a graphic a person reads, and the chip's word is `fgMuted`
/// on the window canvas rather than on a filled capsule.
struct PersonRoomChip: View {

    let roomID: String?

    var body: some View {
        HStack(spacing: 5) {
            Circle()
                .fill(Color.brandInk)
                .frame(width: 6, height: 6)
                .accessibilityHidden(true)
            Text((roomID ?? PeopleStrings.noRoom).lowercased())
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .lineLimit(1)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .overlay(Capsule().stroke(Color.fgMuted.opacity(0.35), lineWidth: 1))
    }
}
