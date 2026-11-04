import DomovoiKit
import SwiftUI

// ---------------------------------------------------------------------------
// The five history tabs — `SatHistoryTabs.kt`.
//
// Every one of them is a read, three states and a list, which is why they
// share ``RoomsTabState``. What differs is the row, and each row's decisions
// are the package's: which turns belong to a session, how long an answer may
// be before it is cut, which word a masked reminder may be called, what a
// source pill's tone is, and how long is left on a timer.
//
// **No row holds a `Task`.** The cancel and the add are the model's, which
// outlives the row, the tab and the screen (`SatHistoryTabs.kt:294`, `:437`).
// ---------------------------------------------------------------------------

/// The sessions tab — `SatSessionsTab` (`SatHistoryTabs.kt:62`).
///
/// A session a tap expands into its own turns, filtered by `session_id`
/// client-side, which is why this tab makes **both** reads
/// (``RoomsRoomModel/refresh(for:)``).
struct RoomsSessionsView: View {

    let model: RoomsModel
    let reads: RoomsRoomModel

    var body: some View {
        RoomsTabState(read: reads.sessions, empty: RoomsStrings.noSessions, hint: nil) { sessions in
            VStack(alignment: .leading, spacing: 12) {
                ForEach(sessions) { session in
                    RoomsSessionRow(model: model, reads: reads, session: session)
                }
            }
        }
    }
}

/// One session, and its turns while it is open (`SessionRow`,
/// `SatHistoryTabs.kt:84`).
struct RoomsSessionRow: View {

    let model: RoomsModel
    let reads: RoomsRoomModel
    let session: RoomsSession

    private var open: Bool { reads.openSessions.contains(session.id ?? "") }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                let id = session.id ?? ""
                if open {
                    reads.openSessions.remove(id)
                } else {
                    reads.openSessions.insert(id)
                }
            } label: {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(relTime(session.startedAt, nowMs: model.clockMs))
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 8)
                        Text(RoomsStrings.turns(session.intentCount))
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    // `3fa85f64 · 1:12 · person #4`, monospaced because the
                    // first eight characters of an id are compared by eye
                    // against the dashboard's.
                    Text(roomsSessionSubtitle(session))
                        .font(.caption.monospaced())
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(RoomsStrings.last(relTime(session.lastActivity, nowMs: model.clockMs)))
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .frame(minHeight: 44)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(open ? [.isButton, .isSelected] : .isButton)
            if open {
                let turns = roomsTurns(of: session, in: reads.conversations.data ?? [])
                if turns.isEmpty {
                    Text(RoomsStrings.noTurnsRecorded)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    ForEach(turns) { turn in
                        RoomsTurnCard(model: model, reads: reads, turn: turn)
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 12, style: .continuous)
        )
    }
}

/// One turn — `TurnCard` (`SatHistoryTabs.kt:153`).
///
/// The assistant's answer is cut at **130 characters once it is longer than
/// 140** (``roomsTurnText(_:expanded:)``), so expanding one always reveals
/// something.
struct RoomsTurnCard: View {

    let model: RoomsModel
    let reads: RoomsRoomModel
    let turn: RoomsTurn

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let pill = roomsHandlerPill(turn)
        let answer = roomsTurnText(turn.assistantText, expanded: reads.expandedTurns.contains(turn.id))
        return VStack(alignment: .leading, spacing: 6) {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 4) { stamp(pill) }
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 8) { stamp(pill) }
            }
            if let said = turn.userText, !said.isEmpty {
                // The quotes are Android's (`SatHistoryTabs.kt:180`): what a
                // person said is quoted and what the house answered is not.
                Text(RoomsStrings.quoted(said))
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !answer.shown.isEmpty {
                Text(answer.shown)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if answer.truncated {
                HomeLink(
                    reads.expandedTurns.contains(turn.id) ? RoomsStrings.less : RoomsStrings.more
                ) {
                    if reads.expandedTurns.contains(turn.id) {
                        reads.expandedTurns.remove(turn.id)
                    } else {
                        reads.expandedTurns.insert(turn.id)
                    }
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10))
    }

    /// When it was, which handler took it, and what it matched.
    @ViewBuilder
    private func stamp(_ pill: (text: String, tone: HealthTone)) -> some View {
        Text(relTime(turn.at, nowMs: model.clockMs))
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
        HomeTonePill(label: pill.text, tone: pill.tone)
        Text(turn.matchedPath ?? "#\(turn.id)")
            .font(.caption.monospaced())
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
        Spacer(minLength: 0)
    }
}

/// The conversations tab — `SatConversationsTab` (`SatHistoryTabs.kt:208`):
/// every turn in the room, with a search that filters what is already here.
struct RoomsConversationsView: View {

    let reads: RoomsRoomModel

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let found = reads.searchResults
        return VStack(alignment: .leading, spacing: 12) {
            search
            if !(reads.conversations.data ?? []).isEmpty {
                Text(RoomsStrings.countOf(found.shown, found.total))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            RoomsTabState(
                read: reads.conversations,
                empty: RoomsStrings.nothingSaidInThisRoom,
                hint: RoomsStrings.roomHasBeenQuiet
            ) { _ in
                if found.turns.isEmpty {
                    // A search that matched nothing is **not** an empty room,
                    // and says so with the query it ran.
                    VStack(alignment: .leading, spacing: 4) {
                        Text(RoomsStrings.nothingSaidInThisRoom)
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(RoomsStrings.searchHint(reads.searchDraft))
                            .font(.footnote)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    ForEach(found.turns) { turn in
                        RoomsConversationTurn(reads: reads, turn: turn)
                    }
                }
            }
        }
    }

    /// The search box — **a different control at an accessibility size**, for
    /// ``MusicFilters``' measured reason: a one-line field clips its own text
    /// at AX5, and a permanently two-line search box is a worse screen for
    /// everybody who never changes their type size.
    ///
    /// The accessibility variant drops `submitLabel(.search)`: a vertical
    /// field is multi-line and its Return key makes a newline, so the key
    /// would say one thing and do another. Nothing is lost — the list has
    /// always filtered as you type.
    @ViewBuilder
    private var search: some View {
        let text = Binding(get: { reads.searchDraft }, set: { reads.searchDraft = $0 })
        if typeSize.isAccessibilitySize {
            TextField(RoomsStrings.searchThisRoom, text: text, axis: .vertical)
                .lineLimit(2...)
                .padding(.horizontal, 8)
                .padding(.vertical, 10)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
                .overlay { RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary) }
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityLabel(RoomsStrings.searchThisRoom)
        } else {
            TextField(RoomsStrings.searchThisRoom, text: text)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .accessibilityLabel(RoomsStrings.searchThisRoom)
        }
    }
}

/// A turn on the conversations tab: the same card, without a session's clock
/// around it.
struct RoomsConversationTurn: View {

    let reads: RoomsRoomModel
    let turn: RoomsTurn

    var body: some View {
        let pill = roomsHandlerPill(turn)
        let answer = roomsTurnText(turn.assistantText, expanded: reads.expandedTurns.contains(turn.id))
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                HomeTonePill(label: pill.text, tone: pill.tone)
                Spacer(minLength: 0)
            }
            if let said = turn.userText, !said.isEmpty {
                Text(RoomsStrings.quoted(said))
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !answer.shown.isEmpty {
                Text(answer.shown)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if answer.truncated {
                HomeLink(
                    reads.expandedTurns.contains(turn.id) ? RoomsStrings.less : RoomsStrings.more
                ) {
                    if reads.expandedTurns.contains(turn.id) {
                        reads.expandedTurns.remove(turn.id)
                    } else {
                        reads.expandedTurns.insert(turn.id)
                    }
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10))
    }
}

/// The recently-played tab — `SatRecentlyPlayedTab` (`SatHistoryTabs.kt:279`).
///
/// `+ add` is drawn on a row the server says `can_add` for, and **only**
/// there. It is kept although the route's gate is a fulfiller's URL allowlist
/// rather than `can_add` (`domovoi/main.py:5782-5788`), because it does not
/// fail every time — which is where no-misleading-controls bites — and because
/// a refusal shows **the server's own words** rather than "add failed"
/// (``RoomsModel/addToLibrary(_:_:)``).
///
/// ### D55 is this condition, and the server has not landed it yet
///
/// Kamron, answer 55: "no fulfiller means no button and no error: the server
/// computes `can_add` against the fulfiller allowlist, so both phones draw it
/// honestly." Then `can_add` alone is the whole rule and the refusal
/// sentence has nothing left to say — but the change is the **server's**, and
/// the route on this branch still computes `can_add` without the allowlist
/// (`web/backend/api/satellites.py:1040-1043`). This unit had deleted the
/// refusal on the strength of the answer; review round 1 was right that it is
/// premature, so it is back until the server half lands. Nothing here changes
/// on that day: the condition is already `can_add` and only `can_add`.
struct RoomsPlayedView: View {

    let model: RoomsModel
    let reads: RoomsRoomModel
    let room: RoomsSatellite

    var body: some View {
        RoomsTabState(
            read: reads.played,
            empty: RoomsStrings.nothingPlayedYet,
            hint: RoomsStrings.nothingPlayedHint
        ) { rows in
            VStack(alignment: .leading, spacing: 12) {
                ForEach(rows) { row in
                    RoomsPlayedRow(model: model, reads: reads, room: room, row: row)
                }
            }
        }
    }
}

struct RoomsPlayedRow: View {

    let model: RoomsModel
    let reads: RoomsRoomModel
    let room: RoomsSatellite
    let row: RoomsPlayed

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                HomeTonePill(
                    label: row.source ?? RoomsStrings.dash,
                    tone: roomsSourceTone(row.source, capabilities: model.capabilities.data)
                )
                Text(relTime(row.startedAt, nowMs: model.clockMs))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            Text(row.title ?? RoomsStrings.trackFallback)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            if let second = secondLine {
                Text(second)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if row.canAdd {
                add
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 12, style: .continuous)
        )
    }

    /// The artist, else the channel a plugin stamped (`SatHistoryTabs.kt:347`).
    private var secondLine: String? {
        if let artist = row.artist, !artist.isEmpty { return artist }
        if let channel = row.channel, !channel.isEmpty { return channel }
        return nil
    }

    /// `+ add`, or `queued` once this visit has asked for it.
    ///
    /// The mark is optimistic and is **taken back on a refusal**: the row only
    /// truly drops off once the download lands.
    private var add: some View {
        let queued = reads.queuedAdds.contains(row.id)
        return Button(queued ? RoomsStrings.queued : RoomsStrings.add) {
            model.addToLibrary(room, row)
        }
        .buttonStyle(.bordered)
        .tint(Color.brandInk)
        .font(.subheadline)
        .fixedSize(horizontal: false, vertical: true)
        .frame(minHeight: 44)
        .contentShape(.rect)
        .disabled(queued)
    }
}

/// The notes tab — `SatNotesTab` (`SatHistoryTabs.kt:389`). A read and a list;
/// the empty state is the sentence a person says out loud.
struct RoomsNotesView: View {

    let model: RoomsModel
    let reads: RoomsRoomModel

    var body: some View {
        RoomsTabState(
            read: reads.notes,
            empty: RoomsStrings.noNotes,
            hint: RoomsStrings.noNotesHint
        ) { notes in
            VStack(alignment: .leading, spacing: 12) {
                ForEach(notes) { note in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(note.body ?? RoomsStrings.dash)
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(relTime(note.capturedAt, nowMs: model.clockMs))
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        .quaternary.opacity(0.4),
                        in: RoundedRectangle(cornerRadius: 12, style: .continuous)
                    )
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }
}

/// The timers tab — `SatTimersTab` (`SatHistoryTabs.kt:438`): what is running
/// in this room, counting down, with `cancel`.
///
/// **A masked reminder is called ``RoomsStrings/timerWordsHidden`` and nothing
/// else.** The server held those words back on purpose (rule M1,
/// `satellites.py:1090`) and ``roomsTimerLabel(_:)`` checks it first — so this
/// view has no branch of its own that could put a name on such a row.
struct RoomsTimersView: View {

    let model: RoomsModel
    let reads: RoomsRoomModel
    let room: RoomsSatellite

    var body: some View {
        RoomsTabState(read: reads.timers, empty: RoomsStrings.noTimers, hint: nil) { timers in
            VStack(alignment: .leading, spacing: 12) {
                ForEach(timers) { timer in
                    RoomsTimerRow(model: model, reads: reads, room: room, timer: timer)
                }
            }
        }
    }
}

struct RoomsTimerRow: View {

    let model: RoomsModel
    let reads: RoomsRoomModel
    let room: RoomsSatellite
    let timer: RoomsTimer

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // ``RoomsModel/clockMs`` is what redraws this once a second, and it
        // only does so because it reads the **stored** ``RoomsModel/tickedMs``
        // — an `@Observable` dependency. The first revision of this comment
        // claimed ``RoomsModel/extraSec`` did it, which nothing in this
        // subtree reads, and the countdown was frozen from the moment the tab
        // was built (`SatHistoryTabs.kt:447-453` ticks for the same reason).
        let left = roomsRemainingSec(expiresAt: timer.expiresAt, nowMs: model.clockMs)
        return VStack(alignment: .leading, spacing: 6) {
            if typeSize.isAccessibilitySize {
                // The cancel goes under the row rather than beside it: a
                // 44-point button and a wrapping label cannot share a line.
                VStack(alignment: .leading, spacing: 8) {
                    words(left)
                    cancel
                }
            } else {
                HStack(alignment: .top, spacing: 8) {
                    words(left)
                    Spacer(minLength: 8)
                    cancel
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 12, style: .continuous)
        )
    }

    private func words(_ left: Int64?) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(roomsTimerLabel(timer))
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                HomeTonePill(label: roomsTimerKind(timer), tone: .idle)
            }
            Text(roomsFmtRemaining(left))
                .font(.subheadline.monospacedDigit())
                // Under ten minutes it is the warn ink, which is 5.65:1 where
                // `.orange` as text is 2.20:1.
                .foregroundStyle(roomsTimerUrgent(left) ? Color.warnInk : Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            Text(RoomsStrings.fires(relTime(timer.expiresAt, nowMs: model.clockMs)))
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The DELETE is the model's, and a second tap on a row whose request is
    /// in flight does nothing — the double-tap guard Home's timers needed.
    private var cancel: some View {
        Button(RoomsStrings.cancel) {
            model.cancelTimer(room, timer)
        }
        .buttonStyle(.bordered)
        .tint(Color.errInk)
        .font(.subheadline)
        .fixedSize(horizontal: false, vertical: true)
        .frame(minHeight: 44)
        .contentShape(.rect)
        .disabled(reads.cancelling.contains(timer.id))
    }
}
